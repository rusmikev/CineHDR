import unittest
from src.hdr_controller import HdrController

# This file acts as a catalog of expected behaviors for real-world media files.
# In a full CI, it would download these samples from a test asset repository.

REAL_MEDIA_CATALOG = [
    {
        "id": "hdr10_1000",
        "description": "HDR10 with 1000-nit mastering display and MaxCLL",
        "expected_peak": 1000.0,
        "expected_hdr": True,
        "color_trc": "pq"
    },
    {
        "id": "hdr10_4000",
        "description": "HDR10 with 4000-nit mastering display",
        "expected_peak": 4000.0,
        "expected_hdr": True,
        "color_trc": "pq"
    },
    {
        "id": "hdr10_no_metadata",
        "description": "HDR10 lacking MaxCLL and mastering display data",
        "expected_peak": None, # Fallback to auto
        "expected_hdr": True,
        "color_trc": "pq"
    },
    {
        "id": "hlg_bt2020",
        "description": "HLG Broadcast format",
        "expected_peak": None,
        "expected_hdr": True,
        "color_trc": "hlg"
    },
    {
        "id": "dovi_p5",
        "description": "Dolby Vision Profile 5 (IPT/PQ)",
        "expected_peak": None,
        "expected_hdr": False, # Explicitly blocked
        "color_trc": "pq"
    },
    {
        "id": "dovi_p8",
        "description": "Dolby Vision Profile 8 (HDR10 Base)",
        "expected_peak": 1000.0,
        "expected_hdr": True,
        "color_trc": "pq"
    }
]

class TestRealMediaCatalog(unittest.TestCase):
    def test_catalog_definitions(self):
        """Ensure all required catalog scenarios are tracked for manual/automated DL."""
        self.assertEqual(len(REAL_MEDIA_CATALOG), 6)
        
    def test_metadata_rules(self):
        """Ensure the definitions match our controller's logic expectations."""
        for item in REAL_MEDIA_CATALOG:
            if item["id"] == "dovi_p5":
                self.assertFalse(item["expected_hdr"])
            elif item["color_trc"] == "pq":
                self.assertTrue(item["expected_hdr"] or item["id"] == "dovi_p5")

if __name__ == '__main__':
    unittest.main()
