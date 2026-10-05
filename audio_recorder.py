import io
import math
import winsound
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf

class AudioRecorder:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.channels = 1
        self.stream = None
        self.is_recording = False
        self.audio_chunks = []
        self.session_chunks = []
        self._lock = threading.Lock()
        self._buffered_frames = 0
        self.current_level = 0.0  # Normalized 0.0 to 1.0 for waveform
        self.last_rms = 0.0
        self.last_p95 = 0.0
        self.capture_error = False

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.capture_error = True
        if self.is_recording:
            with self._lock:
                if not self.is_recording:
                    return
                block = indata.copy()
                self.audio_chunks.append(block)
                self.session_chunks.append(block)
                self._buffered_frames += len(block)
            # Calculate RMS amplitude for real-time visualizer
            rms = np.sqrt(np.mean(indata**2))
            # Smooth normalization
            norm_level = min(1.0, float(rms * 12.0))
            self.current_level = norm_level

    def start(self, play_cue: bool = True):
        if self.is_recording:
            return

        # Play the start peep before opening the microphone so it can never leak
        # into the recording or be interpreted as speech by Whisper.
        if play_cue:
            try:
                winsound.Beep(1047, 55)
            except Exception:
                pass

        with self._lock:
            self.audio_chunks = []
            self.session_chunks = []
            self._buffered_frames = 0
            self.current_level = 0.0
            self.is_recording = True

        self.capture_error = False
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate, channels=self.channels, dtype="float32",
                callback=self._audio_callback,
                # 20 ms blocks make pause detection react ~30 ms sooner than
                # the old 50 ms blocks while remaining inexpensive at 16 kHz.
                blocksize=int(self.sample_rate * 0.02),
                latency="low",
            )
            self.stream.start()
        except Exception:
            self.is_recording = False
            if self.stream:
                self.stream.close()
                self.stream = None
            raise

    def buffered_duration(self) -> float:
        with self._lock:
            frames = self._buffered_frames
        return frames / float(self.sample_rate)

    def peek_chunk(
        self,
        require_speech: bool = True,
        ignore_leading_seconds: float = 0.0,
    ) -> bytes | None:
        """Snapshot the current undrained buffer without interrupting capture."""
        if not self.is_recording:
            return None
        with self._lock:
            if not self.audio_chunks:
                return None
            chunks = list(self.audio_chunks)
        full_audio = np.concatenate(chunks, axis=0)
        return self._encode_audio(
            full_audio,
            require_speech=require_speech,
            ignore_leading_seconds=ignore_leading_seconds,
        )

    def take_chunk(
        self,
        overlap_seconds: float = 0.0,
        require_speech: bool = True,
        ignore_leading_seconds: float = 0.0,
    ) -> bytes | None:
        """Atomically drain the current buffer while recording continues."""
        if not self.is_recording:
            return None

        with self._lock:
            if not self.audio_chunks:
                return None
            full_audio = np.concatenate(self.audio_chunks, axis=0)
            keep_frames = min(
                len(full_audio),
                max(0, int(self.sample_rate * float(overlap_seconds))),
            )
            self.audio_chunks = (
                [full_audio[-keep_frames:].copy()] if keep_frames else []
            )
            self._buffered_frames = keep_frames

        return self._encode_audio(
            full_audio,
            require_speech=require_speech,
            ignore_leading_seconds=ignore_leading_seconds,
        )

    def detach_session_chunks(self):
        """O(1) handoff of retained session audio for lazy fallback encoding."""
        with self._lock:
            chunks, self.session_chunks = self.session_chunks, []
        return chunks

    def encode_session_chunks(self, chunks, require_speech: bool = True) -> bytes | None:
        """Encode retained chunks only if recovery actually needs full audio."""
        if not chunks:
            return None
        full_audio = np.concatenate(chunks, axis=0)
        return self._encode_audio(
            full_audio,
            require_speech=require_speech,
            ignore_leading_seconds=0.2,
        )

    def full_session_wav(self, require_speech: bool = True) -> bytes | None:
        """Return the complete session audio retained independently of chunk drains."""
        with self._lock:
            chunks = list(self.session_chunks)
        return self.encode_session_chunks(chunks, require_speech=require_speech)

    def stop(self, play_cue: bool = True, require_speech: bool = True) -> bytes | None:
        if not self.is_recording:
            return None

        self.is_recording = False
        if self.stream:
            stream, self.stream = self.stream, None
            try:
                stream.stop()
            finally:
                stream.close()

        if play_cue:
            # Stream is already closed, so the end peep cannot enter the ASR
            # audio. Keep it asynchronous so transcription starts immediately.
            def play_end_peep():
                try:
                    winsound.Beep(659, 65)
                except Exception:
                    pass
            threading.Thread(target=play_end_peep, daemon=True).start()

        self.current_level = 0.0
        with self._lock:
            if not self.audio_chunks:
                return None
            full_audio = np.concatenate(self.audio_chunks, axis=0)
            self.audio_chunks = []
            self._buffered_frames = 0

        return self._encode_audio(full_audio, require_speech=require_speech, ignore_leading_seconds=0.0)

    def _encode_audio(
        self,
        full_audio: np.ndarray,
        require_speech: bool = True,
        ignore_leading_seconds: float = 0.0,
    ) -> bytes | None:
        if len(full_audio) < int(self.sample_rate * 0.2):
            return None

        analysis_start = min(len(full_audio), int(self.sample_rate * ignore_leading_seconds))
        analysis_audio = full_audio[analysis_start:] if analysis_start < len(full_audio) else full_audio
        flat = analysis_audio.reshape(-1)
        self.last_rms = float(np.sqrt(np.mean(flat ** 2))) if flat.size else 0.0
        # last_p95 is retained for compatibility but is not consumed anywhere;
        # percentile calculation was pure per-chunk CPU overhead.
        self.last_p95 = 0.0

        if require_speech:
            frame_size = max(1, int(self.sample_rate * 0.02))
            usable = flat[:len(flat) // frame_size * frame_size]
            frames = usable.reshape(-1, frame_size)
            levels = np.sqrt(np.mean(frames ** 2, axis=1)) if len(frames) else np.array([])
            if np.count_nonzero(levels >= 0.002) < 3:
                return None

        byte_io = io.BytesIO()
        sf.write(byte_io, full_audio, self.sample_rate, format="WAV", subtype="PCM_16")
        byte_io.seek(0)
        return byte_io.getvalue()
