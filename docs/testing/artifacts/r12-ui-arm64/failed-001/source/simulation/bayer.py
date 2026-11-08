"""Deterministic synthetic RGGB frames, not a model of a real sensor."""

from array import array
import math


def generate_bayer(width: int, height: int, exposure_us: int = 10000,
                   gain: float = 1.0, scene: str = 'scene') -> array:
    """Return 14-bit simulated photosites in row-major 16-bit containers.

    The scene, RGGB response, black level and clipping are deliberately simple.
    Equal inputs always generate equal bytes; no claim is made about a sensor.
    """
    if (isinstance(width, bool) or isinstance(height, bool) or
            not isinstance(width, int) or not isinstance(height, int) or
            width < 2 or height < 2):
        raise ValueError("width and height must be integers of at least 2")
    if (isinstance(exposure_us, bool) or not isinstance(exposure_us, int) or
            not 1 <= exposure_us <= 0xffffffff or isinstance(gain, bool) or
            not isinstance(gain, (int, float)) or not math.isfinite(gain) or gain <= 0):
        raise ValueError("exposure must be a positive 32-bit integer and gain must be positive and finite")
    if scene not in ('scene', 'dark', 'flat'):
        raise ValueError('scene must be scene, dark, or flat')
    scale = exposure_us / 10000 * gain
    if not math.isfinite(scale):
        raise ValueError('exposure and gain product is too large')
    result = array('H')
    for y in range(height):
        for x in range(width):
            # Synthetic scene combines a ramp and a checker; each Bayer site
            # has an arbitrary response to make CFA ordering testable.
            if scene == 'dark':
                result.append(64)
                continue
            brightness = 2048 if scene == 'flat' else 256 + (4096 * x // (width - 1)) + (1024 * y // (height - 1))
            if scene == 'scene' and ((x // 16) + (y // 16)) % 2:
                brightness += 512
            response = ((1.0, 0.8), (0.8, 0.6))[y % 2][x % 2]
            signal = brightness * response * scale
            result.append(16383 if signal >= 16319 else 64 + int(signal))
    return result
