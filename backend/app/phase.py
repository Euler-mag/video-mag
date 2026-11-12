"""Phase-based motion magnification using a complex steerable pyramid."""
import cv2
import numpy as np

from .evm import Magnifier, TemporalBandpass


class PhaseMagnifier(Magnifier):
    def __init__(self, **settings):
        super().__init__(**settings)
        if self.mode != "motion":
            raise ValueError("Phase magnification requires motion mode")
        try:
            import pyrtools
        except ImportError as exc:
            raise ValueError("Install pyrtools to use phase magnification") from exc
        self.pyrtools = pyrtools
        self.previous = {}
        self.unwrapped = {}

    def process(self, frame, filtered_phases=None):
        if self.gain == 0:
            return frame.copy()
        image = frame.astype(np.float32) / 255
        luminance = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        height = min(self.levels, max(1, int(np.floor(np.log2(min(luminance.shape)))) - 2))
        pyramid = self.pyrtools.pyramids.SteerablePyramidFreq(
            luminance, height=height, order=3, is_complex=True)
        for key, coeff in pyramid.pyr_coeffs.items():
            if not isinstance(key, tuple):
                continue
            phase = np.angle(coeff)
            if key not in self.previous:
                self.previous[key] = phase.copy()
                self.unwrapped[key] = np.zeros_like(phase)
            else:
                delta = np.angle(np.exp(1j * (phase - self.previous[key])))
                self.unwrapped[key] += delta
                self.previous[key] = phase.copy()
            if key not in self.filters:
                self.filters[key] = TemporalBandpass(self.low, self.high, self.fps, self.order)
            filtered = (self.filters[key].apply(self.unwrapped[key]) if filtered_phases is None
                        else filtered_phases[key])
            magnitude = np.abs(coeff)
            weighted = cv2.GaussianBlur(filtered * magnitude, (0, 0), 1.0)
            weight = cv2.GaussianBlur(magnitude, (0, 0), 1.0)
            filtered = weighted / np.maximum(weight, 1e-6)
            pyramid.pyr_coeffs[key] = coeff * np.exp(1j * self.gain * filtered)
        reconstructed = pyramid.recon_pyr()
        result = image + (reconstructed - luminance)[..., None]
        self.clipped = float(np.mean((result < 0) | (result > 1)))
        return np.rint(np.clip(result, 0, 1) * 255).astype(np.uint8)
