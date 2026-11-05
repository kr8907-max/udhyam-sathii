import { useState, useRef, useEffect, useCallback } from 'react';

export function useVoiceRecorder({
  onRecordingComplete,
  onVolumeChange,
  onSpeechStart,
  onSpeechEnd,
  silenceThresholdMs = 2000,
  minSpeechThreshold = 0.025,
  maxDurationMs = 60000,
} = {}) {
  const [isRecording, setIsRecording] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [volume, setVolume] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(null);

  const mediaRecorderRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const streamRef = useRef(null);
  const scriptProcessorRef = useRef(null);
  const pcmBuffersRef = useRef([]);
  const animFrameRef = useRef(null);
  const timerIntervalRef = useRef(null);

  const chunksRef = useRef([]);
  const hasSpokenRef = useRef(false);
  const silenceStartRef = useRef(null);
  const isCancelledRef = useRef(false);
  const startTimeRef = useRef(0);
  const speechStartedRef = useRef(false);

  const onVolumeChangeRef = useRef(onVolumeChange);
  const onSpeechStartRef = useRef(onSpeechStart);
  const onSpeechEndRef = useRef(onSpeechEnd);
  useEffect(() => { onVolumeChangeRef.current = onVolumeChange; }, [onVolumeChange]);
  useEffect(() => { onSpeechStartRef.current = onSpeechStart; }, [onSpeechStart]);
  useEffect(() => { onSpeechEndRef.current = onSpeechEnd; }, [onSpeechEnd]);

  const cleanupAudio = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = null;
    }
    if (scriptProcessorRef.current) {
      try {
        scriptProcessorRef.current.disconnect();
      } catch (e) {}
      scriptProcessorRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      try {
        audioContextRef.current.close();
      } catch (e) {}
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch (e) {}
    }
    setIsRecording(false);
    setIsSpeaking(false);
    setVolume(0);
    speechStartedRef.current = false;
    cleanupAudio();
  }, [cleanupAudio]);

  const cancelRecording = useCallback(() => {
    isCancelledRef.current = true;
    stopRecording();
  }, [stopRecording]);

  const startRecording = useCallback(async () => {
    setError(null);
    isCancelledRef.current = false;
    hasSpokenRef.current = false;
    speechStartedRef.current = false;
    silenceStartRef.current = null;
    chunksRef.current = [];
    setDuration(0);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioContextRef.current = audioCtx;

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.4;
      source.connect(analyser);
      analyserRef.current = analyser;

      pcmBuffersRef.current = [];
      const bufferSize = 4096;
      try {
        const processor = audioCtx.createScriptProcessor
          ? audioCtx.createScriptProcessor(bufferSize, 1, 1)
          : null;
        if (processor) {
          processor.onaudioprocess = (e) => {
            if (isCancelledRef.current) return;
            const inputData = e.inputBuffer.getChannelData(0);
            pcmBuffersRef.current.push(new Float32Array(inputData));
          };
          source.connect(processor);
          processor.connect(audioCtx.destination);
          scriptProcessorRef.current = processor;
        }
      } catch (procErr) {
        console.warn('ScriptProcessor initialization failed, using MediaRecorder fallback:', procErr);
      }

      let mimeType = 'audio/webm;codecs=opus';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        if (MediaRecorder.isTypeSupported('audio/webm')) {
          mimeType = 'audio/webm';
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          mimeType = 'audio/mp4';
        } else {
          mimeType = '';
        }
      }

      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        if (!isCancelledRef.current) {
          if (pcmBuffersRef.current.length > 0) {
            try {
              const totalSamples = pcmBuffersRef.current.reduce((acc, b) => acc + b.length, 0);
              const merged = new Float32Array(totalSamples);
              let offset = 0;
              for (const buf of pcmBuffersRef.current) {
                merged.set(buf, offset);
                offset += buf.length;
              }
              const downsampled = downsampleBuffer(merged, audioCtx.sampleRate, 16000);
              const wavBlob = encodeWAV(downsampled, 16000);
              pcmBuffersRef.current = [];
              if (onRecordingComplete) {
                onRecordingComplete(wavBlob);
              }
            } catch (wavErr) {
              console.warn('WAV encoding failed, falling back to MediaRecorder WebM:', wavErr);
              if (chunksRef.current.length > 0 && onRecordingComplete) {
                const finalBlob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
                onRecordingComplete(finalBlob);
              }
            }
          } else if (chunksRef.current.length > 0 && onRecordingComplete) {
            const finalBlob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
            onRecordingComplete(finalBlob);
          }
        }
        cleanupAudio();
      };

      recorder.start(100);
      startTimeRef.current = Date.now();
      setIsRecording(true);

      timerIntervalRef.current = setInterval(() => {
        setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 200);

      const dataArray = new Uint8Array(analyser.frequencyBinCount);

      const checkAudioLevel = () => {
        if (!analyserRef.current || !streamRef.current) return;

        analyserRef.current.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i] * dataArray[i];
        }
        const rms = Math.sqrt(sum / dataArray.length) / 255;
        setVolume(rms);

        if (onVolumeChangeRef.current) {
          onVolumeChangeRef.current(rms);
        }

        const now = Date.now();
        const elapsed = now - startTimeRef.current;

        if (rms > minSpeechThreshold) {
          hasSpokenRef.current = true;
          setIsSpeaking(true);
          silenceStartRef.current = null;

          if (!speechStartedRef.current) {
            speechStartedRef.current = true;
            if (onSpeechStartRef.current) {
              onSpeechStartRef.current();
            }
          }
        } else {
          setIsSpeaking(false);
          if (hasSpokenRef.current) {
            if (!silenceStartRef.current) {
              silenceStartRef.current = now;
            } else {
              const silentDuration = now - silenceStartRef.current;
              if (silentDuration >= silenceThresholdMs && elapsed > 800) {
                if (speechStartedRef.current && onSpeechEndRef.current) {
                  onSpeechEndRef.current();
                }
                stopRecording();
                return;
              }
            }
          }
        }

        if (elapsed > maxDurationMs) {
          if (speechStartedRef.current && onSpeechEndRef.current) {
            onSpeechEndRef.current();
          }
          stopRecording();
          return;
        }

        animFrameRef.current = requestAnimationFrame(checkAudioLevel);
      };

      animFrameRef.current = requestAnimationFrame(checkAudioLevel);
    } catch (err) {
      console.error('Microphone access failed:', err);
      setError(err.message || 'Microphone access denied. Please grant permission in your browser.');
      setIsRecording(false);
      cleanupAudio();
    }
  }, [minSpeechThreshold, silenceThresholdMs, maxDurationMs, onRecordingComplete, stopRecording, cleanupAudio]);

  useEffect(() => {
    return () => {
      cleanupAudio();
    };
  }, [cleanupAudio]);

  return {
    isRecording,
    isSpeaking,
    volume,
    duration,
    error,
    startRecording,
    stopRecording,
    cancelRecording,
  };
}

function downsampleBuffer(buffer, inputSampleRate, outputSampleRate = 16000) {
  if (inputSampleRate === outputSampleRate) return buffer;
  const sampleRateRatio = inputSampleRate / outputSampleRate;
  const newLength = Math.round(buffer.length / sampleRateRatio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

function encodeWAV(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([view], { type: 'audio/wav' });
}

function writeString(view, offset, string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

export default useVoiceRecorder;
