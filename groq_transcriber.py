import re
import time
import threading
import unicodedata
from difflib import SequenceMatcher

import httpx
from langid.langid import LanguageIdentifier, model as LANGID_MODEL

try:
    from .dialect_prompts import get_dialect_prompt
except ImportError:
    from dialect_prompts import get_dialect_prompt

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

TECHNICAL_GLOSSARY = (
    "GitHub", "ChatGPT", "Groq", "Whisper", "Windows", "Python",
    "Salla", "Noon", "Amazon", "Shopify", "Cloudflare", "GitLab",
    "JavaScript", "TypeScript", "Android", "OpenAI", "API", "JSON",
)

class GroqAPIError(RuntimeError):
    def __init__(self, status_code, retry_after=None):
        self.status_code = status_code
        try:
            self.retry_after = min(5.0, max(0.0, float(retry_after or 0)))
        except (ValueError, TypeError):
            self.retry_after = 0.0
        super().__init__(f"Groq API Error ({status_code})")


class TranscriptLanguageError(ValueError):
    """ASR returned text outside the one language allowed for this recording."""


class GroqTranscriber:
    NO_SPEECH_THRESHOLD = 0.65

    @staticmethod
    def _build_prompt(language: str, dialect: str, custom_prompt: str = "") -> str:
        """Use only user-supplied spelling context; never auto-inject a language-biased prompt."""
        # The previous automatic Gazan phrase list could strongly bias Whisper
        # toward Arabic after a bad language lock. Auto must be completely
        # prompt-free because its source language is not known yet. Explicit
        # modes may still use a deliberate user-supplied spelling prompt.
        if language == "auto":
            return ""
        return custom_prompt.strip()

    def __init__(self, api_key: str = "", timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        # Keep the TLS connection alive across an entire dictation (and usually
        # across recordings). httpx defaults to a short keep-alive expiry, which
        # can otherwise add DNS/TCP/TLS setup back into later chunks.
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=4,
                keepalive_expiry=300.0,
            ),
        )
        self._prewarm_lock = threading.Lock()
        self._prewarm_inflight = False
        self._last_prewarm = 0.0

        # Local language ID is used only as a final safety rail for Latin-script
        # output. It runs in a few milliseconds and catches cases where Whisper
        # says "English" in metadata but actually emits French/Spanish/etc.
        self._langid = LanguageIdentifier.from_modelstring(
            LANGID_MODEL, norm_probs=True
        )
        self._langid.set_languages([
            "en", "fr", "de", "es", "it", "pt", "nl", "tr", "pl", "ro",
            "sv", "da", "no", "fi", "cs", "hr", "id", "ms", "vi",
        ])
        self._langid_lock = threading.Lock()

    def prewarm_async(self, force: bool = False):
        """Warm DNS/TLS/HTTP connection without delaying microphone capture."""
        if not self.api_key:
            return
        now = time.monotonic()
        with self._prewarm_lock:
            if self._prewarm_inflight:
                return
            if not force and now - self._last_prewarm < 20.0:
                return
            self._prewarm_inflight = True

        key = self.api_key

        def work():
            try:
                # A lightweight authenticated read is enough to populate the
                # client's connection pool. It carries no audio/user content.
                response = self._client.get(
                    GROQ_MODELS_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=4.0,
                )
                if response.status_code < 500:
                    self._last_prewarm = time.monotonic()
            except Exception:
                pass
            finally:
                with self._prewarm_lock:
                    self._prewarm_inflight = False

        threading.Thread(target=work, daemon=True, name="groq-prewarm").start()

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    @classmethod
    def _extract_text(cls, result: dict) -> tuple[str, bool]:
        """Return transcript text and whether it was suppressed as probable no-speech."""
        raw_text = result.get("text", "").strip()
        segments = result.get("segments") or []
        no_speech_probs = []
        for segment in segments:
            try:
                value = segment.get("no_speech_prob")
                if value is not None:
                    no_speech_probs.append(float(value))
            except (TypeError, ValueError, AttributeError):
                continue

        probable_silence = bool(no_speech_probs) and min(no_speech_probs) >= cls.NO_SPEECH_THRESHOLD
        return ("" if probable_silence else raw_text), probable_silence

    @staticmethod
    def _normalized_language(value) -> str:
        value = str(value or "").strip().casefold()
        aliases = {
            "arabic": "ar", "ara": "ar", "ar": "ar",
            "english": "en", "eng": "en", "en": "en",
        }
        return aliases.get(value, value[:2] if len(value) >= 2 else value)

    @staticmethod
    def _script_profile(text: str) -> tuple[bool, bool, int]:
        has_ar = bool(re.search(r"[\u0621-\u064a]", text or ""))
        has_lat = bool(re.search(r"[A-Za-z]", text or ""))
        words = re.findall(r"[A-Za-z\u0621-\u064a]+", text or "")
        return has_ar, has_lat, len(words)

    @staticmethod
    def _strict_script_mismatch(language: str, text: str) -> bool:
        """Reject output containing letters from a different writing system."""
        value = text or ""
        if not value.strip():
            return False

        # Technical product names are allowed inside Arabic dictation, but no
        # arbitrary Latin-language sentence is. This preserves common names
        # such as GitHub/ChatGPT/API without reopening cross-language drift.
        allowed_ar_latin = {
            item.casefold() for item in TECHNICAL_GLOSSARY
        }

        arabic_tokens = 0
        unknown_latin_tokens = 0
        unknown_latin_run = 0
        max_unknown_latin_run = 0

        for token in re.findall(r"\S+", value):
            letters = [ch for ch in token if ch.isalpha()]
            if not letters:
                continue
            names = [unicodedata.name(ch, "") for ch in letters]

            if language == "en":
                # English dictation may contain any Latin letter (including
                # accented names), but never Arabic/Cyrillic/CJK/etc.
                if any("LATIN" not in name for name in names):
                    return True

            elif language == "ar":
                if all("ARABIC" in name for name in names):
                    arabic_tokens += 1
                    unknown_latin_run = 0
                    continue
                if not all("LATIN" in name for name in names):
                    return True

                cleaned = re.sub(r"[^A-Za-z0-9]+", "", token).casefold()
                ascii_letters = "".join(
                    ch for ch in token if ch.isascii() and ch.isalpha()
                )
                known_name = cleaned in allowed_ar_latin
                known_acronym = bool(
                    ascii_letters
                    and len(ascii_letters) <= 6
                    and ascii_letters.isupper()
                )
                if known_name or known_acronym:
                    unknown_latin_run = 0
                    continue

                # Permit a single unfamiliar product/name token inside a
                # clearly Arabic sentence (e.g. "افتح Slack هلقيت"), but never
                # a Latin phrase/run or Latin-majority output.
                unknown_latin_tokens += 1
                unknown_latin_run += 1
                max_unknown_latin_run = max(
                    max_unknown_latin_run, unknown_latin_run
                )

        if language == "ar" and unknown_latin_tokens:
            # Arabic dictation commonly includes short English product names or
            # phrases. Treat that as code-switching, not a language failure.
            # Reject only when Latin text dominates strongly or there is no
            # Arabic evidence at all.
            if arabic_tokens == 0:
                return True
            if unknown_latin_tokens > max(3, int(arabic_tokens * 1.5)):
                return True
            if max_unknown_latin_run >= 5 and unknown_latin_tokens >= arabic_tokens:
                return True

        return False

    @classmethod
    def _language_mismatch(cls, expected: str, text: str) -> bool:
        has_ar, has_lat, word_count = cls._script_profile(text)
        if word_count < 3:
            return False
        if expected == "ar":
            return has_lat and not has_ar
        if expected == "en":
            return has_ar and not has_lat
        return False

    def _english_text_is_confidently_non_english(self, text: str) -> bool:
        """Catch Latin-script foreign-language output without slowing normal text."""
        has_ar, has_lat, word_count = self._script_profile(text)
        if has_ar or not has_lat or word_count < 5:
            return False
        try:
            with self._langid_lock:
                code, probability = self._langid.classify(text)
            return code != "en" and float(probability) >= 0.92
        except Exception:
            # Language ID is an additional safety rail, never a hard dependency
            # for successful dictation.
            return False

    def transcript_matches_language(self, language: str, text: str) -> bool:
        """Fast local validation for the session's single allowed language."""
        if not (text or "").strip():
            return True
        if self._strict_script_mismatch(language, text):
            return False
        if language == "en" and self._english_text_is_confidently_non_english(text):
            return False
        return True

    @staticmethod
    def _correction_is_conservative(original: str, corrected: str) -> bool:
        """Reject model output that looks like rewriting or translation."""
        original = original.strip()
        corrected = corrected.strip()
        if not original or not corrected:
            return False
        if original == corrected:
            return True

        # Obvious-ASR correction should remain textually close to the source.
        ratio = SequenceMatcher(None, original, corrected).ratio()
        min_ratio = 0.52 if len(original) < 24 else 0.68
        if ratio < min_ratio:
            return False

        original_words = original.split()
        corrected_words = corrected.split()
        allowed_word_delta = max(2, int(len(original_words) * 0.25))
        if abs(len(corrected_words) - len(original_words)) > allowed_word_delta:
            return False

        def scripts(text: str) -> tuple[bool, bool]:
            has_ar = any("\u0600" <= ch <= "\u06ff" for ch in text)
            has_lat = any(("a" <= ch.lower() <= "z") for ch in text)
            return has_ar, has_lat

        before_scripts = scripts(original)
        after_scripts = scripts(corrected)

        # Never allow a correction to erase one side of Arabic/English
        # code-switching; that is a common signature of accidental translation.
        if before_scripts == (True, True):
            if after_scripts != (True, True):
                return False
        elif before_scripts == (True, False) and after_scripts == (False, True):
            return False
        elif before_scripts == (False, True) and after_scripts == (True, False):
            return False

        return True

    @staticmethod
    def _collapse_exact_repeats(text: str) -> str:
        """Remove only immediate repeated phrases of 2+ words."""
        words = text.split()
        if len(words) < 4:
            return text.strip()

        def norm(token: str) -> str:
            return re.sub(r"[^\w]+", "", token, flags=re.UNICODE).casefold()

        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(words):
                max_n = min(8, (len(words) - i) // 2)
                removed = False
                for n in range(max_n, 1, -1):
                    left = [norm(x) for x in words[i:i + n]]
                    right = [norm(x) for x in words[i + n:i + 2 * n]]
                    if all(left) and left == right:
                        del words[i + n:i + 2 * n]
                        changed = True
                        removed = True
                        break
                if not removed:
                    i += 1
        # A second conservative pass removes immediately duplicated long
        # words (e.g. "lightweight lightweight") while preserving common
        # intentional emphasis such as "very very" or "no no".
        deduped = []
        for word in words:
            current = norm(word)
            previous = norm(deduped[-1]) if deduped else ""
            if (
                previous
                and current == previous
                and len(current) >= 6
            ):
                continue
            deduped.append(word)

        return " ".join(deduped).strip()

    @staticmethod
    def _fix_glossary_typos(text: str) -> str:
        """Fix only very close Latin-script matches to known technical terms."""
        aliases = {"GitHup": "GitHub", "Git Hub": "GitHub", "Chat GPT": "ChatGPT", "Open AI": "OpenAI"}
        for source, target in aliases.items():
            text = re.sub(r"(?<![\w@./-])" + re.escape(source) + r"(?![\w@./-])", target, text)
        return text

    def correct_obvious_mistakes(self, text: str) -> dict:
        """Zero-network correction: only exact repetition and high-confidence glossary typos."""
        start = time.perf_counter()
        original = (text or "").strip()
        if not original:
            return {"text": original, "changed": False, "latency": 0.0}

        # These two transformations are deliberately deterministic and
        # narrow: delete exact immediate repetitions and repair only
        # high-confidence glossary spellings. No generative rewriting is used.
        corrected = original  # Repetition can be intentional; only audio overlap may be deduplicated.
        corrected = self._fix_glossary_typos(corrected)

        return {
            "text": corrected,
            "changed": corrected != original,
            "latency": time.perf_counter() - start,
        }
    def transcribe(
        self,
        wav_bytes: bytes,
        model: str = "whisper-large-v3-turbo",
        language: str = "auto",
        dialect: str = "gazan",
        custom_prompt: str = ""
    ) -> dict:
        if not self.api_key:
            raise ValueError("Groq API Key is not set. Please configure your key in settings.")

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        # Keep auto-detection unbiased. In mixed Arabic/English mode, an
        # Arabic-heavy prompt can make Whisper normalize English words into Arabic.
        prompt = self._build_prompt(language, dialect, custom_prompt)

        # Keep verbose_json even after language lock: segment-level
        # no_speech_prob is an important hallucination/silence safety signal.
        data = {
            "model": model,
            "response_format": "verbose_json",
            "temperature": "0.0"
        }

        if language and language != "auto":
            data["language"] = language

        if prompt:
            data["prompt"] = prompt

        files = {
            "file": ("recording.wav", wav_bytes, "audio/wav")
        }

        start_time = time.perf_counter()
        resp = self._client.post(
            GROQ_TRANSCRIPTION_URL,
            headers=headers,
            data=data,
            files=files,
        )
        elapsed = time.perf_counter() - start_time

        if resp.status_code != 200:
            err_msg = resp.text
            try:
                err_json = resp.json()
                if "error" in err_json and "message" in err_json["error"]:
                    err_msg = err_json["error"]["message"]
            except Exception:
                pass
            raise GroqAPIError(resp.status_code, resp.headers.get("retry-after"))

        result = resp.json()
        transcribed_text, filtered_no_speech = self._extract_text(result)
        detected = self._normalized_language(result.get("language"))

        # Keep a tiny amount of Whisper confidence metadata for the one-time
        # Auto-language probe. It is not used to rewrite transcript text.
        weighted_logprob = 0.0
        logprob_weight = 0.0
        no_speech_values = []
        for segment in result.get("segments") or []:
            try:
                value = segment.get("avg_logprob")
                if value is not None:
                    start = float(segment.get("start", 0.0) or 0.0)
                    end = float(segment.get("end", start) or start)
                    weight = max(0.01, end - start)
                    weighted_logprob += float(value) * weight
                    logprob_weight += weight
                no_speech = segment.get("no_speech_prob")
                if no_speech is not None:
                    no_speech_values.append(float(no_speech))
            except (TypeError, ValueError, AttributeError):
                continue
        avg_logprob = (
            weighted_logprob / logprob_weight
            if logprob_weight > 0
            else None
        )
        max_no_speech_prob = max(no_speech_values) if no_speech_values else None

        suspected_language_mismatch = bool(
            language == "auto"
            and not filtered_no_speech
            and detected in ("ar", "en")
            and self._language_mismatch(detected, transcribed_text)
        )

        # Explicit mode is now a hard single-language rail. Groq's reported
        # language must agree when present, and the output may not contain a
        # foreign writing system. This is deliberately stricter than ordinary
        # Whisper behavior because live chunks must never switch languages.
        if language in ("ar", "en") and not filtered_no_speech:
            detected_norm = self._normalized_language(result.get("language"))
            metadata_mismatch = bool(
                detected_norm and detected_norm != language
            )
            content_mismatch = not self.transcript_matches_language(
                language, transcribed_text
            )
            # Wrong forced-language decodes can look perfectly valid in the
            # requested script while actually translating/mangling the audio.
            # Low Whisper confidence is therefore a warning, but not a hard
            # failure by itself: noisy correct speech can also score below this
            # threshold. The session guard will verify low-confidence chunks
            # with one Auto pass before allowing them to type.
            confidence_mismatch = bool(
                avg_logprob is not None and float(avg_logprob) < -0.75
            )
            if metadata_mismatch or content_mismatch:
                label = "Arabic" if language == "ar" else "English"
                raise TranscriptLanguageError(
                    f"{label} mode produced unsafe language output; "
                    "result withheld. Please repeat."
                )

        return {
            "text": transcribed_text,
            "latency": elapsed,
            "model": model,
            "language": language,
            "detected_language": result.get("language"),
            "avg_logprob": avg_logprob,
            "max_no_speech_prob": max_no_speech_prob,
            "filtered_no_speech": filtered_no_speech,
            "suspected_language_mismatch": suspected_language_mismatch,
            "low_confidence": bool(
                language in ("ar", "en")
                and not filtered_no_speech
                and avg_logprob is not None
                and float(avg_logprob) < -0.75
            ),
        }