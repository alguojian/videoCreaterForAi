class LocalVoiceError(Exception):
    """Base error for the local voice pipeline."""


class InvalidVoiceProfileError(LocalVoiceError):
    """Raised when a voice profile is missing or unsafe."""


class WorkerExecutionError(LocalVoiceError):
    """Raised when a local worker cannot produce its requested result."""


class SubtitleMappingError(LocalVoiceError):
    """Raised when alignment data cannot be mapped to valid subtitle cues."""


class AudioProcessingError(LocalVoiceError):
    """Raised when a WAV block cannot be validated or assembled."""
