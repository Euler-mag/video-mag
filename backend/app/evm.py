"""Bounded-memory causal Eulerian magnification, with BGR frames."""
import cv2
import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


class TemporalBandpass:
    def __init__(self, low, high, fps, order=2):
        self.sos = butter(order, [low, high], btype="bandpass", fs=fps, output="sos")
        self.state = None

    def apply(self, image):
        sample = image.astype(np.float64, copy=False)[None]
        if self.state is None:
            initial = sosfilt_zi(self.sos)
            self.state = initial.reshape(initial.shape + (1,) * image.ndim) * image
        result, self.state = sosfilt(self.sos, sample, axis=0, zi=self.state)
        return result[0].astype(np.float32)


def pyramid(image, levels=4):
    layers = [image]
    for _ in range(levels):
        if min(layers[-1].shape[:2]) < 8:
            break
        layers.append(cv2.pyrDown(layers[-1]))
    return layers


class Magnifier:
    def __init__(self, mode="color", gain=20, low=.7, high=3, fps=30, levels=4, order=2, min_detail=0):
        if mode not in ("color", "motion"):
            raise ValueError("Choose color or motion")
        if not 0 < low < high < fps / 2:
            raise ValueError("Require 0 < low < high < FPS / 2")
        if not 0 <= gain <= 100:
            raise ValueError("Gain must be between 0 and 100")
        self.mode, self.gain = mode, gain
        self.low, self.high, self.fps = low, high, fps
        if not 2 <= levels <= 6 or not 1 <= order <= 4:
            raise ValueError("Use 2–6 pyramid levels and filter order 1–4")
        if not 0 <= min_detail <= 256:
            raise ValueError("Minimum detail must be between 0 and 256 pixels")
        self.min_detail = min_detail
        self.levels, self.order = levels, order
        self.filters = {}
        self.clipped = 0.

    def bandpass(self, key, image):
        if key not in self.filters:
            self.filters[key] = TemporalBandpass(self.low, self.high, self.fps, self.order)
        return self.filters[key].apply(image)

    def layer_gain(self, index, count):
        base = self.gain * index / (count - 2)
        if not self.min_detail:
            return base
        wavelength = 2 ** (index + 1)
        return base * min(1.0, wavelength / self.min_detail)

    def process(self, frame):
        if self.gain == 0:
            self.clipped = 0
            return frame.copy()
        image = frame.astype(np.float32) / 255
        gaussian = pyramid(image, self.levels)
        if self.mode == "color":
            signal = self.bandpass(0, gaussian[-1])
            for target in reversed(gaussian[:-1]):
                signal = cv2.pyrUp(signal, dstsize=(target.shape[1], target.shape[0]))
            result = image + self.gain * signal
        else:
            # Laplacian EVM: intermediate bands, with conservative attenuation
            # at finer scales. Finest band and coarse residual remain intact.
            layers = []
            for index, target in enumerate(gaussian[:-1]):
                expanded = cv2.pyrUp(gaussian[index + 1], dstsize=(target.shape[1], target.shape[0]))
                layer = target - expanded
                if index > 0:
                    scale_gain = self.layer_gain(index, len(gaussian))
                    layer = layer + scale_gain * self.bandpass(index, layer)
                layers.append(layer)
            result = gaussian[-1]
            for layer in reversed(layers):
                result = cv2.pyrUp(result, dstsize=(layer.shape[1], layer.shape[0])) + layer
        self.clipped = float(np.mean((result < 0) | (result > 1)))
        return np.rint(np.clip(result, 0, 1) * 255).astype(np.uint8)


def demo_frame(t, width=640, height=360):
    """Independent 1.2 Hz color variation and subpixel displacement."""
    image = np.full((height, width, 3), 30, dtype=np.float32)
    image[60:height - 60, 40:width // 2 - 20] = [105, 145 + 2 * np.sin(2 * np.pi * 1.2 * t), 190]
    x = np.arange(width, dtype=np.float32)
    edge = width * .72 + .6 * np.sin(2 * np.pi * 1.2 * t)
    stripe = 50 + 155 * np.exp(-((x - edge) / 9) ** 2)
    image[60:height - 60, width // 2 + 20:] = stripe[width // 2 + 20:, None]
    return np.rint(image).astype(np.uint8)
