# e2e fixture provenance

`tests/fixtures/meeting/{mic,remote}.wav` is the audio used by the opt-in
real-audio test `tests/test_transcribe_e2e.py`. It is **not** a real meeting —
it is built from public-domain (**CC0**) Portuguese word recordings from
[Lingua Libre](https://lingualibre.org) via Wikimedia Commons, so no private
voice ships in the repo.

All source files are licensed **CC0 1.0** (public-domain dedication); no
attribution is required, but sources are listed here for traceability.

Each track is its speaker's clips decoded and resampled to 16 kHz mono s16 WAV
(matching `scribe capture` output) and concatenated with 0.25 s gaps.

## `mic.wav` (label "You") — speaker MedK1
- "Acre" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-MedK1-Acre.wav)
- "abacateiro" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-MedK1-abacateiro.wav)
- "abadia" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-MedK1-abadia.wav)
- "abaixar" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-MedK1-abaixar.wav)
- "abandonado" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-MedK1-abandonado.wav)

## `remote.wav` (label "Them") — speaker Jessie Edwin Lawrence
- "abacaxizeiro" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-Jessie_Edwin_Lawrence-abacaxizeiro.wav)
- "abano" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-Jessie_Edwin_Lawrence-abano.wav)
- "aborrecimento" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-Jessie_Edwin_Lawrence-aborrecimento.wav)
- "absinto" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-Jessie_Edwin_Lawrence-absinto.wav)
- "absolutismo" — [Commons](https://commons.wikimedia.org/wiki/File:LL-Q5146_%28por%29-Jessie_Edwin_Lawrence-absolutismo.wav)

## Rebuilding
Download the source WAVs from the Commons pages above, decode + resample to
16 kHz mono with PyAV (bundled with `faster-whisper`), and concatenate per
speaker. All sources verified `LicenseShortName == "CC0"` via the Commons API
before use.
