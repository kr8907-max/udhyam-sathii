import { useState, useEffect, useRef, useCallback } from 'react';
import { chatApi } from '../services/api';

export const WAKE_WORD_REGEX = /(?:\b(?:hey|hay|ay|hai|hi|hello|ok|okay|namaste|sun|oye)\b[\s,.]*)?(?:\b(?:mira|meera|meara|miraa|meeraa|meerha|mirah|mehra|meira|miira|myra|mirror|mera|mirha|meerah|myraa|meeral|miral)\b|मीरा|मिरा)/i;

export function extractTrailingPrompt(fullText) {
  if (!fullText) return '';
  const match = fullText.match(WAKE_WORD_REGEX);
  if (!match) return '';

  const matchEnd = match.index + match[0].length;
  const trailing = fullText.slice(matchEnd).replace(/^[,.\s]+/, '').trim();
  return trailing;
}

export function matchWakeWord(text) {
  if (!text) return false;
  return WAKE_WORD_REGEX.test(text);
}

export function useWakeWord({
  onWakeWordDetected,
  onSpeechRecognized,
  enabledByDefault = false,
  cooldownMs = 2000,
  minSpeechThreshold = 0.022,
  silenceThresholdMs = 500,
  maxUtteranceMs = 3500,
} = {}) {
  const [isEnabled, setIsEnabled] = useState(() => {
    try {
      const stored = localStorage.getItem('udyam_wake_word_mira_enabled');
      if (stored !== null) return stored === 'true';
      return false;
    } catch {
      return false;
    }
  });

  const [isListening, setIsListening] = useState(false);
  const [error, setError] = useState(null);
  const [lastDetected, setLastDetected] = useState(null);

  const hasMediaDevices = typeof window !== 'undefined' && 
    Boolean(navigator?.mediaDevices?.getUserMedia);
  const isSupported = hasMediaDevices;
  const engineType = 'sarvam_bhashini_vad';

  const shouldListenRef = useRef(isEnabled);
  const lastTriggerTimeRef = useRef(0);
  const onWakeWordRef = useRef(onWakeWordDetected);
  onWakeWordRef.current = onWakeWordDetected;
  const onSpeechRecognizedRef = useRef(onSpeechRecognized);
  onSpeechRecognizedRef.current = onSpeechRecognized;

  const vadStreamRef = useRef(null);
  const vadAudioCtxRef = useRef(null);
  const vadAnalyserRef = useRef(null);
  const vadMediaRecorderRef = useRef(null);
  const vadAnimFrameRef = useRef(null);
  const vadChunksRef = useRef([]);
  const isCapturingRef = useRef(false);
  const captureStartTimeRef = useRef(0);
  const silenceStartRef = useRef(null);
  const isTranscribingRef = useRef(false);
  const isStoppingRef = useRef(false);

  useEffect(() => {
    shouldListenRef.current = isEnabled;
    try {
      localStorage.setItem('udyam_wake_word_mira_enabled', String(isEnabled));
    } catch {
    }
  }, [isEnabled]);

  const handleDetection = useCallback((transcript, detectedLang = null) => {
    if (!transcript) return;
    const now = Date.now();

    console.debug(`[useWakeWord] 📝 Backend transcript: "${transcript}" | Regex test: ${WAKE_WORD_REGEX.test(transcript)}`);

    if (matchWakeWord(transcript)) {
      if (now - lastTriggerTimeRef.current < cooldownMs) {
        console.debug('[useWakeWord] ⏳ Cooldown active, ignoring duplicate trigger');
        return;
      }

      lastTriggerTimeRef.current = now;
      const trailingQuery = extractTrailingPrompt(transcript);
      setLastDetected({
        timestamp: now,
        transcript,
        trailingQuery,
        engine: engineType,
        language: detectedLang,
      });

      console.info(`[useWakeWord] 🎙️ "Mira" detected via backend ASR! Trailing prompt:`, trailingQuery || '(none)');

      if (onWakeWordRef.current) {
        onWakeWordRef.current({
          phrase: 'Mira',
          transcript,
          trailingQuery,
          engine: engineType,
          language: detectedLang,
        });
      }
    } else {
      if (onSpeechRecognizedRef.current) {
        onSpeechRecognizedRef.current(transcript);
      }
    }
  }, [cooldownMs, engineType]);

  const cleanupVadResources = useCallback(() => {
    if (vadAnimFrameRef.current) {
      cancelAnimationFrame(vadAnimFrameRef.current);
      vadAnimFrameRef.current = null;
    }
    if (vadMediaRecorderRef.current && vadMediaRecorderRef.current.state !== 'inactive') {
      try {
        vadMediaRecorderRef.current.stop();
      } catch (e) {}
    }
    vadMediaRecorderRef.current = null;
    if (vadStreamRef.current) {
      vadStreamRef.current.getTracks().forEach((t) => t.stop());
      vadStreamRef.current = null;
    }
    if (vadAudioCtxRef.current && vadAudioCtxRef.current.state !== 'closed') {
      try {
        vadAudioCtxRef.current.close();
      } catch (e) {}
    }
    vadAudioCtxRef.current = null;
    vadAnalyserRef.current = null;
    vadChunksRef.current = [];
    isCapturingRef.current = false;
  }, []);

  useEffect(() => {
    if (!isEnabled) {
      isStoppingRef.current = true;
      cleanupVadResources();
      setIsListening(false);
      return;
    }

    let isCancelled = false;
    isStoppingRef.current = false;

    async function initVad() {
      try {
        setError(null);
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        if (isCancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }

        vadStreamRef.current = stream;
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioCtx();
        vadAudioCtxRef.current = audioCtx;

        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.3;
        source.connect(analyser);
        vadAnalyserRef.current = analyser;

        setIsListening(true);
        console.info('[useWakeWord] 🎤 VAD + Backend ASR engine started. Say "Mira" to activate.');

        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        const vadLoop = () => {
          if (!shouldListenRef.current || isCancelled) return;

          analyser.getByteTimeDomainData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            const val = (dataArray[i] - 128) / 128;
            sum += val * val;
          }
          const rms = Math.sqrt(sum / dataArray.length);
          const now = Date.now();

          if (rms > minSpeechThreshold) {
            silenceStartRef.current = null;

            if (!isCapturingRef.current && !isTranscribingRef.current) {
              isCapturingRef.current = true;
              captureStartTimeRef.current = now;
              vadChunksRef.current = [];

              try {
                const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                  ? 'audio/webm;codecs=opus'
                  : 'audio/webm';
                const recorder = new MediaRecorder(stream, { mimeType: mime });
                vadMediaRecorderRef.current = recorder;

                recorder.ondataavailable = (e) => {
                  if (e.data && e.data.size > 0) {
                    vadChunksRef.current.push(e.data);
                  }
                };

                recorder.onstop = async () => {
                  const chunks = vadChunksRef.current;
                  vadChunksRef.current = [];
                  isCapturingRef.current = false;

                  if (chunks.length > 0 && shouldListenRef.current && !isCancelled) {
                    const audioBlob = new Blob(chunks, { type: mime });
                    if (audioBlob.size > 2000) {
                      isTranscribingRef.current = true;
                      try {
                        console.debug(`[useWakeWord] 🔄 Sending ${(audioBlob.size / 1024).toFixed(1)}KB audio to backend /stt...`);
                        const sttResult = await chatApi.transcribeAudio(audioBlob, '');
                        const transcript = sttResult?.transcript || sttResult?.text || '';
                        const detectedLang = sttResult?.language || sttResult?.detected_language_code || null;
                        const provider = sttResult?.provider || 'unknown';
                        if (transcript.trim()) {
                          console.debug(`[useWakeWord] 📡 Backend (${provider}): "${transcript.trim()}" [lang=${detectedLang}]`);
                          handleDetection(transcript.trim(), detectedLang);
                        }
                      } catch (err) {
                        console.debug('[useWakeWord] Transcription error:', err?.message);
                      } finally {
                        isTranscribingRef.current = false;
                      }
                    }
                  }
                };

                recorder.start(150);
              } catch (recErr) {
                console.warn('[useWakeWord] Recorder failed:', recErr);
                isCapturingRef.current = false;
              }
            }
          } else if (isCapturingRef.current) {
            if (!silenceStartRef.current) {
              silenceStartRef.current = now;
            } else {
              const silenceDuration = now - silenceStartRef.current;
              const totalUtterance = now - captureStartTimeRef.current;

              if (silenceDuration >= silenceThresholdMs || totalUtterance >= maxUtteranceMs) {
                if (vadMediaRecorderRef.current && vadMediaRecorderRef.current.state === 'recording') {
                  try {
                    vadMediaRecorderRef.current.stop();
                  } catch (e) {}
                }
              }
            }
          }

          vadAnimFrameRef.current = requestAnimationFrame(vadLoop);
        };

        vadAnimFrameRef.current = requestAnimationFrame(vadLoop);
      } catch (err) {
        if (!isCancelled) {
          console.error('[useWakeWord] Microphone access failed:', err);
          setError('Microphone permission required for "Mira" voice activation.');
          setIsEnabled(false);
          setIsListening(false);
        }
      }
    }

    initVad();

    return () => {
      isCancelled = true;
      cleanupVadResources();
    };
  }, [
    isEnabled,
    minSpeechThreshold,
    silenceThresholdMs,
    maxUtteranceMs,
    handleDetection,
    cleanupVadResources,
  ]);

  const toggleWakeWord = useCallback(() => {
    setIsEnabled((prev) => {
      const next = !prev;
      shouldListenRef.current = next;
      return next;
    });
  }, []);

  const enableWakeWord = useCallback(() => {
    shouldListenRef.current = true;
    setIsEnabled(true);
  }, []);

  const disableWakeWord = useCallback(() => {
    shouldListenRef.current = false;
    setIsEnabled(false);
  }, []);

  return {
    isSupported,
    isEnabled,
    isListening,
    error,
    lastDetected,
    engineType,
    toggleWakeWord,
    enableWakeWord,
    disableWakeWord,
  };
}

export default useWakeWord;
