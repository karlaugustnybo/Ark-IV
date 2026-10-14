import unittest

from ark_pipeline.extinction_status import extinction_status


class ExtinctionStatusTests(unittest.TestCase):
    def test_verified_non_ex_categories_overrule_both_ott_extinction_flags(self):
        for category in ["LC", "NT", "VU", "EN", "CR", "DD", "EW"]:
            for flags in ["extinct", "extinct_inherited,incertae_sedis"]:
                status = extinction_status(category, flags)
                self.assertFalse(status["effective_extinct"])
                self.assertTrue(status["ott_extinction_overridden"])
                self.assertEqual(status["extinction_status_source"], "IUCN")

    def test_iucn_ex_controls_extinction_even_without_ott_flag(self):
        for flags in [None, "", "extinct"]:
            status = extinction_status("EX", flags)
            self.assertTrue(status["effective_extinct"])
            self.assertFalse(status["ott_extinction_overridden"])
            self.assertEqual(status["extinction_status_source"], "IUCN")

    def test_missing_and_ne_assessments_do_not_assert_extant_status(self):
        for category in [None, "NE"]:
            status = extinction_status(category, "extinct")
            self.assertIsNone(status["effective_extinct"])
            self.assertEqual(status["extinction_status_source"], "unresolved")
            self.assertTrue(status["ott_extinct_flag"])

    def test_flag_tokens_are_exact_and_invalid_categories_are_rejected(self):
        self.assertFalse(extinction_status("EN", "not_extinct")["ott_extinct_flag"])
        with self.assertRaisesRegex(ValueError, "normalized"):
            extinction_status("invalid", "extinct")


if __name__ == "__main__":
    unittest.main()
