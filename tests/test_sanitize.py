"""``bumm_guard.sanitize``: text from a report or a job is made printable before it is shown."""

import unittest

from bumm_guard.sanitize import TEXT_MAX, clean, clean_str, is_plain, strip_control


class SanitizeTests(unittest.TestCase):
    def test_ascii_control_characters_are_removed(self):
        self.assertEqual(clean("a\nb\rc\td\x1b[8me\x00f\x7fg"), "abcd[8mefg")

    def test_c1_format_and_separator_characters_are_removed(self):
        # CSI (C1), right-to-left override, zero-width space, line and paragraph separators
        self.assertEqual(clean("a\x9b31mb‮c​d e f"), "a31mbcdef")

    def test_lone_surrogate_is_removed(self):
        self.assertEqual(clean("a\ud800b"), "ab")

    def test_ordinary_text_is_kept(self):
        for text in ("with migrate", "оновлення з міграцією", "escrow v1 → v2: 12 deals", ""):
            self.assertEqual(clean(text), text)

    def test_long_text_is_cut_with_an_ellipsis(self):
        out = clean("x" * 500)
        self.assertEqual(out, "x" * TEXT_MAX + "...")
        self.assertEqual(clean("x" * TEXT_MAX), "x" * TEXT_MAX)
        self.assertEqual(clean("x" * 500, limit=10), "x" * 10 + "...")

    def test_cut_is_made_after_the_removal(self):
        self.assertEqual(clean("\n" * 300 + "ok"), "ok")

    def test_non_strings_go_through_str(self):
        self.assertEqual(clean(None), "None")
        self.assertEqual(clean(7), "7")
        self.assertEqual(clean({"a": "b\nc"}), "{'a': 'b\\nc'}")

    def test_clean_str_keeps_non_strings(self):
        self.assertIsNone(clean_str(None))
        self.assertEqual(clean_str(7), 7)
        self.assertEqual(clean_str("a\nb"), "ab")

    def test_strip_control_does_not_cut(self):
        self.assertEqual(strip_control("x" * 500 + "\n"), "x" * 500)

    def test_is_plain(self):
        self.assertTrue(is_plain("migrate"))
        self.assertFalse(is_plain("migrate\n"))
        self.assertFalse(is_plain("x" * (TEXT_MAX + 1)))
        self.assertTrue(is_plain("x" * 256, 256))
        self.assertFalse(is_plain(None))


if __name__ == "__main__":
    unittest.main()
