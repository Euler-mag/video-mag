"""Streaming Riesz phase magnification on CUDA."""
import numpy as np
import torch
import torch.nn.functional as F
from .evm_cuda import CudaMagnifier


class RieszMagnifier(CudaMagnifier):
    def __init__(self, **settings):
        super().__init__(**settings)
        self.reference = {}
        self.phase = {}
        self.derivative_x = torch.tensor([[-.5, 0, .5]], device="cuda").reshape(1, 1, 1, 3)
        self.derivative_y = self.derivative_x.transpose(-1, -2)

    @torch.inference_mode()
    def process(self, frame):
        if self.gain == 0:
            self.clipped = 0
            return frame.copy()
        image = torch.from_numpy(np.ascontiguousarray(frame)).to("cuda").permute(2, 0, 1)[None].float() / 255
        luminance = (image[:, 0:1] * .114 + image[:, 1:2] * .587 + image[:, 2:3] * .299).repeat(1, 3, 1, 1)
        gaussian = [luminance]
        for _ in range(self.levels):
            if min(gaussian[-1].shape[-2:]) < 8:
                break
            gaussian.append(self.down(gaussian[-1]))
        bands = []
        for index, target in enumerate(gaussian[:-1]):
            band = (target - self.up(gaussian[index + 1], target))[:, :1]
            rx = F.conv2d(F.pad(band, (1, 1, 0, 0), mode="replicate"), self.derivative_x)
            ry = F.conv2d(F.pad(band, (0, 0, 1, 1), mode="replicate"), self.derivative_y)
            quadrature = torch.cat([band, rx, ry], dim=1)
            if index not in self.reference:
                self.reference[index] = quadrature
                self.phase[index] = torch.zeros_like(rx)
            old = self.reference[index]
            dot = (quadrature * old).sum(dim=1, keepdim=True)
            cross_x = rx * old[:, :1] - band * old[:, 1:2]
            cross_y = ry * old[:, :1] - band * old[:, 2:3]
            norm = torch.sqrt(rx * rx + ry * ry + 1e-12)
            signed_cross = (cross_x * rx + cross_y * ry) / norm
            self.phase[index] = self.phase[index] + torch.atan2(signed_cross, dot + 1e-12)
            self.reference[index] = quadrature
            filtered = self.bandpass(index, self.phase[index])
            amplitude = torch.sqrt((quadrature * quadrature).sum(dim=1, keepdim=True) + 1e-12)
            smoothed = F.avg_pool2d(filtered * amplitude, 5, stride=1, padding=2) / (F.avg_pool2d(amplitude, 5, stride=1, padding=2) + 1e-6)
            angle = self.gain * smoothed
            modified = band * torch.cos(angle) - norm * torch.sin(angle)
            bands.append(modified.repeat(1, 3, 1, 1))
        result = gaussian[-1]
        for band in reversed(bands):
            result = self.up(result, band) + band
        result = image + result - luminance
        self.clipped = ((result < 0) | (result > 1)).float().mean().item()
        return (result.clamp(0, 1) * 255).round().byte()[0].permute(1, 2, 0).cpu().numpy()
