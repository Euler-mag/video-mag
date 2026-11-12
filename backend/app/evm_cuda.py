"""CUDA implementation of the classical EVM pyramid and temporal filters."""
import numpy as np
import torch
import torch.nn.functional as F
from scipy.signal import butter, sosfilt_zi

from .evm import Magnifier


class CudaMagnifier(Magnifier):
    def __init__(self, **settings):
        super().__init__(**settings)
        if not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable")
        torch.empty(1, device="cuda")
        self.sos = butter(self.order, [self.low, self.high], btype="bandpass",
                          fs=self.fps, output="sos")
        self.initial = sosfilt_zi(self.sos)
        self.states = {}
        kernel = torch.tensor([1, 4, 6, 4, 1], device="cuda", dtype=torch.float32) / 16
        self.kernel = (kernel[:, None] * kernel[None, :])[None, None].repeat(3, 1, 1, 1)

    def down(self, image):
        return F.conv2d(F.pad(image, (2, 2, 2, 2), mode="reflect"), self.kernel, stride=2, groups=3)

    def up(self, image, target):
        height, width = target.shape[-2:]
        expanded = torch.zeros((1, 3, image.shape[-2] * 2, image.shape[-1] * 2),
                               device="cuda", dtype=torch.float32)
        expanded[:, :, ::2, ::2] = image
        result = F.conv2d(F.pad(expanded, (2, 2, 2, 2), mode="reflect"), self.kernel * 4, groups=3)
        return result[:, :, :height, :width]

    def bandpass(self, key, image):
        if key not in self.states:
            self.states[key] = [
                [image * float(self.initial[i, 0]), image * float(self.initial[i, 1])]
                for i in range(len(self.sos))
            ]
        result = image
        for i, (b0, b1, b2, a0, a1, a2) in enumerate(self.sos):
            z1, z2 = self.states[key][i]
            output = result * float(b0 / a0) + z1
            self.states[key][i] = [result * float(b1 / a0) - output * float(a1 / a0) + z2,
                                   result * float(b2 / a0) - output * float(a2 / a0)]
            result = output
        return result

    @torch.inference_mode()
    def process(self, frame):
        if self.gain == 0:
            self.clipped = 0
            return frame.copy()
        image = torch.from_numpy(np.ascontiguousarray(frame)).to("cuda").permute(2, 0, 1)[None].float() / 255
        gaussian = [image]
        for _ in range(self.levels):
            if min(gaussian[-1].shape[-2:]) < 8:
                break
            gaussian.append(self.down(gaussian[-1]))
        if self.mode == "color":
            signal = self.bandpass(0, gaussian[-1])
            for target in reversed(gaussian[:-1]):
                signal = self.up(signal, target)
            result = image + self.gain * signal
        else:
            layers = []
            for index, target in enumerate(gaussian[:-1]):
                layer = target - self.up(gaussian[index + 1], target)
                if index > 0:
                    layer = layer + self.layer_gain(index, len(gaussian)) * self.bandpass(index, layer)
                layers.append(layer)
            result = gaussian[-1]
            for layer in reversed(layers):
                result = self.up(result, layer) + layer
        self.clipped = ((result < 0) | (result > 1)).float().mean().item()
        return (result.clamp(0, 1) * 255).round().byte()[0].permute(1, 2, 0).cpu().numpy()
