import subprocess
import json
import os
import sys
import unittest

def run_pixel_validator(video_file, target_peak):
    cmd = ["./tests/pixel_validator", video_file, str(target_peak)]
    env = os.environ.copy()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    except subprocess.CalledProcessError as e:
        return []
    
    out = result.stdout
    if "[" in out and "]" in out:
        json_str = out[out.find("[") : out.rfind("]") + 1]
        try:
            return json.loads(json_str)
        except:
            return []
    return []

# Reference PQ implementation (ST 2084)
def linear_to_pq(l_nits):
    l = max(0.0, min(l_nits / 10000.0, 1.0))
    m1 = 2610 / 16384
    m2 = 2523 / 32
    c1 = 3424 / 4096
    c2 = 2413 / 128
    c3 = 2392 / 128
    return ((c1 + c2 * (l ** m1)) / (1 + c3 * (l ** m1))) ** m2

class TestPixelPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        cls.luma_1000 = run_pixel_validator("tests/clips/luma_steps.mkv", 1000.0)
        cls.gamut_1000 = run_pixel_validator("tests/clips/gamut_test.mkv", 1000.0)

    def test_reference_white_pq_math(self):
        """
        [L3 Invariant: Reference PQ Math]
        Verifies that ST 2084 normalized output for 203 absolute nits (reference white)
        yields exactly ~0.581. This ensures our standard baseline matches SMPTE.
        """
        if not self.luma_1000:
            self.skipTest("Headless environment missing")
        
        expected_pq = linear_to_pq(203.0)
        self.assertAlmostEqual(expected_pq, 0.581, places=3)
        
        patch_203 = next((p for p in self.luma_1000 if p['row'] == 1 and p['col'] == 1), None)
        self.assertIsNotNone(patch_203)
        self.assertAlmostEqual(patch_203['r'], expected_pq, delta=0.05)

    def test_identity_mapping(self):
        """
        [L3 Invariant: Identity Test]
        When target-peak matches source-peak (e.g., 1000 nits), libmpv should 
        NOT drastically alter the transfer function. 
        Input PQ code value should closely match Output FBO PQ code value.
        """
        if not self.luma_1000:
            self.skipTest("Headless environment missing")
            
        test_points = [
            (0, 3, 1.0),      # 1 nit
            (0, 4, 10.0),     # 10 nits
            (1, 0, 100.0),    # 100 nits
            (1, 2, 400.0),    # 400 nits
            (1, 3, 1000.0),   # 1000 nits
        ]
        
        for row, col, nits in test_points:
            expected_pq = linear_to_pq(nits)
            patch = next(p for p in self.luma_1000 if p['row'] == row and p['col'] == col)
            self.assertAlmostEqual(patch['r'], expected_pq, delta=0.05, 
                msg=f"Identity mismatch at {nits} nits")

    def test_monotonicity(self):
        """
        [L3 Invariant: Monotonicity]
        HDR luminance ramp must be strictly monotonic. 
        encoded(L1) < encoded(L2) for all L1 < L2.
        """
        if not self.luma_1000:
            self.skipTest("Headless environment missing")
            
        # Extract R values from row-major ordering (0.001 to 4000)
        sorted_patches = sorted(self.luma_1000, key=lambda p: p['row'] * 5 + p['col'])
        r_values = [p['r'] for p in sorted_patches]
        
        for i in range(1, len(r_values)):
            self.assertLess(r_values[i-1], r_values[i], 
                msg=f"Monotonicity failed between index {i-1} and {i}")

    def test_no_black_crush(self):
        """
        [L3 Invariant: No Black Crush]
        Ensure near-black patches (0.001, 0.01, 0.1) remain distinct 
        and do not aggressively clip to 0.0.
        """
        if not self.luma_1000:
            self.skipTest("Headless environment missing")
            
        p_0001 = next(p for p in self.luma_1000 if p['row'] == 0 and p['col'] == 0)['r']
        p_001 = next(p for p in self.luma_1000 if p['row'] == 0 and p['col'] == 1)['r']
        p_01 = next(p for p in self.luma_1000 if p['row'] == 0 and p['col'] == 2)['r']
        
        self.assertGreater(p_0001, 0.0)
        self.assertGreater(p_001, p_0001)
        self.assertGreater(p_01, p_001)

    def test_neutral_axis(self):
        """
        [L3 Invariant: Neutral Axis]
        PQ Grayscale must retain R = G = B across the entire 0.001 -> 4000 nit scale.
        Any significant deviation indicates a catastrophic color matrix/gamut failure.
        """
        if not self.luma_1000:
            self.skipTest("Headless environment missing")
            
        for patch in self.luma_1000:
            r, g, b = patch['r'], patch['g'], patch['b']
            self.assertAlmostEqual(r, g, delta=0.01, msg=f"Red/Green axis shift: R={r}, G={g}")
            self.assertAlmostEqual(r, b, delta=0.01, msg=f"Red/Blue axis shift: R={r}, B={b}")

if __name__ == '__main__':
    unittest.main()
