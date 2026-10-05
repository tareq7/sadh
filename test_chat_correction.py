import threading
import time
import unittest
from unittest.mock import Mock
from chat_correction import ArabicChatCorrector, acceptable_correction, clean_chat_context

CONTEXT = {"is_whatsapp": True, "own_messages_identified": True,
           "topic": "Sending order details", "own_style": "Casual Gazan Arabic",
           "own_examples": ["تمام يا زلمة، ببعثلك التفاصيل هلقيت"]}

class ChatCorrectionTests(unittest.TestCase):
    def test_arabic_typo_with_dialect_preserved(self):
        self.assertTrue(acceptable_correction("تمام رح ابعتلك التفاصل هلقيت", "تمام رح أبعتلك التفاصيل هلقيت"))

    def test_context_ocr_cannot_corrupt_valid_gazan_word(self):
        self.assertFalse(acceptable_correction("هلقيت", "هقيت"))

    def test_formalization_rejected(self):
        self.assertFalse(acceptable_correction("تمام رح ابعتلك التفاصل هلقيت", "حسنا سوف أرسل إليك التفاصيل الآن"))

    def test_valid_gazan_word_cannot_be_degraded_by_context_ocr(self):
        self.assertFalse(acceptable_correction("تمام بعطيك التفاصيل هلقيت", "تمام بعطيك التفاصيل هقيت"))

    def test_negation_preserved(self):
        self.assertFalse(acceptable_correction("ما بدي ألغي الطلب", "أنا بدي ألغي الطلب"))
        self.assertFalse(acceptable_correction("لا تبعت الطلب هلقيت", "تبعت الطلب هلقيت"))

    def test_numbers_cannot_come_from_context(self):
        self.assertFalse(acceptable_correction("ابعتلي تفاصيل الطلب", "ابعتلي تفاصيل الطلب 100"))
        self.assertFalse(acceptable_correction("بدي مقاس 100", "بدي مقاس 98"))

    def test_english_is_not_translated(self):
        self.assertFalse(acceptable_correction("افتح GitHub وبعدين ابعتلي الرابط", "افتح جيتهاب وبعدين ابعتلي الرابط"))

    def test_other_partys_tone_not_used_if_uncertain(self):
        context = clean_chat_context(dict(CONTEXT, own_messages_identified=False))
        self.assertEqual(context["own_style"], "")
        self.assertEqual(context["own_examples"], [])
        self.assertEqual(context["topic"], CONTEXT["topic"])

    def test_not_a_chat_skips(self):
        corrector = ArabicChatCorrector(Mock(), "model", "url")
        self.assertEqual(corrector.correct("مرحبا", {}, "dummy"), "مرحبا")
        corrector.client.post.assert_not_called()

    def test_timeout_returns_original(self):
        corrector = ArabicChatCorrector(Mock(), "model", "url")
        release = threading.Event()
        corrector._request = lambda *args: (release.wait(1), "changed")[1]
        start = time.monotonic()
        self.assertEqual(corrector.correct("مرحبا", CONTEXT, "dummy", budget=.05), "مرحبا")
        self.assertLess(time.monotonic()-start, .2)
        release.set()

    def test_cancellation_returns_original(self):
        corrector = ArabicChatCorrector(Mock(), "model", "url")
        self.assertEqual(corrector.correct("مرحبا", CONTEXT, "dummy", cancelled=lambda: True), "مرحبا")
        corrector.client.post.assert_not_called()

    def test_context_is_bounded(self):
        context = clean_chat_context(dict(CONTEXT, topic="x"*3000, own_examples=["y"*400]*8))
        self.assertEqual(len(context["topic"]), 300)
        self.assertEqual(len(context["own_examples"]), 2)
        self.assertTrue(all(len(x)<=140 for x in context["own_examples"]))

    def test_bad_model_output_ignored(self):
        corrector = ArabicChatCorrector(Mock(), "model", "url")
        corrector._request = Mock(return_value="Certainly! I will send it.")
        self.assertEqual(corrector.correct("ما بدي أبعت الطلب", CONTEXT, "dummy"), "ما بدي أبعت الطلب")

    def test_valid_model_correction_used(self):
        corrector = ArabicChatCorrector(Mock(), "model", "url")
        corrector._request = Mock(return_value="تمام رح أبعتلك التفاصيل هلقيت")
        self.assertEqual(corrector.correct("تمام رح ابعتلك التفاصل هلقيت", CONTEXT, "dummy"), "تمام رح أبعتلك التفاصيل هلقيت")

if __name__ == '__main__':
    unittest.main(verbosity=2)
