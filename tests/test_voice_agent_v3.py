
from __future__ import annotations
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from backend.app.main import app
from backend.app.config import settings
from backend.app.core.action_registry import ACTION_REGISTRY_SCHEMA, ACTION_NAMES
from backend.app.core import sarvam_client
from backend.app.core.sarvam_client import (
    extract_language_code_from_response,
    SarvamDetectionError,
    SarvamUnavailableError,
)
from backend.app.core import bhashini_client
from backend.app.core.bhashini_client import (
    BhashiniClient,
    BhashiniLanguageUnsupportedError,
    BhashiniUnavailableError,
    normalize_bhashini_lang,
)
from backend.app.core import audio_chat_service as acs_mod
from backend.app.core.audio_chat_service import (
    audio_chat_service,
    build_degraded_language_notice_response,
    normalize_lang,
    _telemetry,
)
from backend.app.core.chat_service import LLMReplyResult

client = TestClient(app)



def test_gate1_sarvam_extract_language_code_conventions():
    assert extract_language_code_from_response({"language_code": "hi-IN", "transcript": "नमस्ते"}) == "hi-IN"
    
    assert extract_language_code_from_response({"source_language_code": "ta-IN", "transcript": "வணக்கம்"}) == "ta-IN"
    
    assert extract_language_code_from_response({"detected_language_code": "mr-IN", "transcript": "नमस्कार"}) == "mr-IN"
    
    assert extract_language_code_from_response({"detected_language": "te-IN", "text": "హలో"}) == "te-IN"
    
    assert extract_language_code_from_response({"language": "kn-IN", "transcript": "ನಮಸ್ಕಾರ"}) == "kn-IN"

    assert extract_language_code_from_response({}) is None
    assert extract_language_code_from_response({"transcript": "no language here"}) is None
    assert extract_language_code_from_response({"language_code": "   "}) is None
    assert extract_language_code_from_response(None) is None
    assert extract_language_code_from_response("invalid-string") is None



@pytest.mark.asyncio
async def test_gate2_bhashini_coverage_and_gap_handling():
    client_test = BhashiniClient()

    supported_12 = ["as", "bn", "en", "gu", "hi", "kn", "ml", "mr", "or", "pa", "ta", "te"]
    
    mock_pipeline_response = {
        "pipelineResponseConfig": [
            {
                "taskType": "asr",
                "config": [
                    {
                        "serviceId": f"ai4bharat/conformer-{lang}",
                        "language": {"sourceLanguage": lang},
                    }
                    for lang in supported_12
                ],
            }
        ],
        "pipelineInferenceAPIEndPoint": {
            "callbackUrl": "https://dhruva-api.bhashini.gov.in/services/inference/pipeline",
            "inferenceApiKey": {
                "name": "Authorization",
                "value": "mock_inference_key_xyz",
            },
        },
    }

    with patch.object(client_test, "_get_credentials", return_value=("mock_user", "mock_key")), \
         patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_pipeline_response
        mock_post.return_value = mock_resp

        cfg_hi = await client_test._get_config("asr", "hi")
        assert cfg_hi["serviceId"] == "ai4bharat/conformer-hi"
        assert cfg_hi["callback_url"] == "https://dhruva-api.bhashini.gov.in/services/inference/pipeline"
        assert cfg_hi["inference_key"] == "mock_inference_key_xyz"

        cfg_ta = await client_test._get_config("asr", "ta")
        assert cfg_ta["serviceId"] == "ai4bharat/conformer-ta"

        with pytest.raises(BhashiniLanguageUnsupportedError) as exc_info:
            await client_test._get_config("asr", "sat")
        assert "not supported" in str(exc_info.value)



@pytest.mark.asyncio
@pytest.mark.parametrize("lang_detect,norm_code,transcript,sample_reply", [
    ("hi-IN", "hi", "मुझे एमएसएमई योजनाएं दिखाओ", "यहाँ आपके लिए उपयुक्त एमएसएमई योजनाएं हैं।"),
    ("ta-IN", "ta", "திட்டங்களை காட்டு", "உங்களுக்கான அரசு திட்டங்கள் இங்கே உள்ளன."),
    ("mr-IN", "mr", "शासकीय योजना दाखवा", "येथे आपल्या व्यवसायासाठी शासकीय योजना आहेत."),
])
async def test_gate3_happy_path_3_languages(lang_detect, norm_code, transcript, sample_reply):
    fake_audio = b"\x00\x01\x02\x03" * 50
    initial_bhashini_total = _telemetry["bhashini_total"]

    mock_detect = AsyncMock(return_value=lang_detect)
    mock_transcribe = AsyncMock(return_value=transcript)
    mock_synthesize = AsyncMock(return_value=b"RIFF_WAV_HEADER_DATA_STREAM")
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text=sample_reply,
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=250.0,
        sources=["Schemes Engine"],
    ))

    with patch.object(acs_mod.sarvam_client, "detect_language", mock_detect), \
         patch.object(acs_mod.bhashini_client, "transcribe", mock_transcribe), \
         patch.object(acs_mod.bhashini_client, "synthesize", mock_synthesize), \
         patch.object(acs_mod.chat_service, "generate_grounded_reply", mock_llm):

        turn = await audio_chat_service.process_voice_turn_v2(
            audio_bytes=fake_audio,
            context={"currentTab": "schemes"},
        )

        assert turn["tier_used"] == "bhashini"
        assert turn["detected_language"] == lang_detect
        assert turn["language"] == norm_code
        assert turn["user_transcript"] == transcript
        assert turn["reply"] == sample_reply
        assert turn["audio_base64"].startswith("data:audio/wav;base64,")
        assert _telemetry["bhashini_total"] == initial_bhashini_total + 1

        mock_detect.assert_awaited_once_with(fake_audio, audio_format="wav")
        mock_transcribe.assert_awaited_once_with(fake_audio, language_code=lang_detect)
        mock_synthesize.assert_awaited_once()



@pytest.mark.asyncio
async def test_gate4_forced_bhashini_tts_failure_exact_1_llm_call():
    fake_audio = b"\x00\x01\x02\x03" * 50
    initial_tts_fallback_total = _telemetry["bhashini_asr_gtts_tts_total"]

    mock_detect = AsyncMock(return_value="hi-IN")
    mock_transcribe = AsyncMock(return_value="लोन की जानकारी दें")
    mock_synthesize = AsyncMock(side_effect=BhashiniUnavailableError("Bhashini TTS 503 Service Unavailable"))
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="यहाँ मुद्रा ऋण की जानकारी है।",
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=200.0,
        sources=[],
    ))
    mock_gtts = MagicMock(return_value={
        "audio_base64": "data:audio/mp3;base64,GTTS_FALLBACK_AUDIO_STREAM",
        "language": "hi",
        "language_name": "Hindi",
        "latency_s": 0.15,
    })

    with patch.object(acs_mod.sarvam_client, "detect_language", mock_detect), \
         patch.object(acs_mod.bhashini_client, "transcribe", mock_transcribe), \
         patch.object(acs_mod.bhashini_client, "synthesize", mock_synthesize), \
         patch.object(acs_mod.chat_service, "generate_grounded_reply", mock_llm), \
         patch.object(audio_chat_service, "text_to_speech", mock_gtts):

        turn = await audio_chat_service.process_voice_turn_v2(audio_bytes=fake_audio)

        assert turn["tier_used"] == "bhashini_asr_gtts_tts"
        assert turn["detected_language"] == "hi-IN"
        assert turn["reply"] == "यहाँ मुद्रा ऋण की जानकारी है।"
        assert turn["audio_base64"] == "data:audio/mp3;base64,GTTS_FALLBACK_AUDIO_STREAM"
        assert _telemetry["bhashini_asr_gtts_tts_total"] == initial_tts_fallback_total + 1

        mock_llm.assert_called_once()
        mock_gtts.assert_called_once()



@pytest.mark.asyncio
async def test_gate5_forced_sarvam_detection_failure_fallback():
    fake_audio = b"\x00\x01\x02\x03" * 50
    initial_fallback_total = _telemetry["fallback_total"]

    mock_detect = AsyncMock(side_effect=SarvamDetectionError("Sarvam detection timed out after 4000ms"))
    mock_whisper = MagicMock(return_value={
        "transcript": "व्यवसाय ऋण कैसे मिलेगा",
        "language": "hi",
        "detected_language_code": "hi",
        "language_name": "Hindi",
        "latency_s": 0.45,
    })
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="व्यवसाय ऋण के लिए आप पीएमएमवाई में आवेदन कर सकते हैं।",
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=280.0,
        sources=[],
    ))
    mock_gtts = MagicMock(return_value={
        "audio_base64": "data:audio/mp3;base64,FALLBACK_GTTS_AUDIO",
        "language": "hi",
        "language_name": "Hindi",
        "latency_s": 0.12,
    })

    with patch.object(acs_mod.sarvam_client, "detect_language", mock_detect), \
         patch.object(audio_chat_service, "transcribe_audio", mock_whisper), \
         patch.object(acs_mod.chat_service, "generate_grounded_reply", mock_llm), \
         patch.object(audio_chat_service, "text_to_speech", mock_gtts):

        turn = await audio_chat_service.process_voice_turn_v2(audio_bytes=fake_audio)

        assert turn["tier_used"] == "fallback"
        assert turn["user_transcript"] == "व्यवसाय ऋण कैसे मिलेगा"
        assert turn["reply"] == "व्यवसाय ऋण के लिए आप पीएमएमवाई में आवेदन कर सकते हैं।"
        assert turn["audio_base64"] == "data:audio/mp3;base64,FALLBACK_GTTS_AUDIO"
        assert _telemetry["fallback_total"] == initial_fallback_total + 1

        mock_whisper.assert_called_once()
        mock_llm.assert_called_once()
        mock_gtts.assert_called_once()



@pytest.mark.asyncio
async def test_gate6_forced_bhashini_language_gap():
    fake_audio = b"\x00\x01\x02\x03" * 50
    initial_gap_count = _telemetry["bhashini_language_gap"]
    initial_fallback_total = _telemetry["fallback_total"]

    mock_detect = AsyncMock(return_value="doi-IN")
    mock_transcribe = AsyncMock(
        side_effect=BhashiniLanguageUnsupportedError("Language 'doi' is not supported by Bhashini ASR pipeline")
    )
    mock_whisper = MagicMock(return_value={
        "transcript": "Dogri speech transcribed by Whisper",
        "language": "hi",
        "detected_language_code": "hi",
        "language_name": "Hindi",
        "latency_s": 0.4,
    })
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="Fallback reply generated.",
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=180.0,
        sources=[],
    ))
    mock_gtts = MagicMock(return_value={
        "audio_base64": "data:audio/mp3;base64,GTTS_DATA",
        "language": "hi",
        "language_name": "Hindi",
        "latency_s": 0.1,
    })

    with patch.object(acs_mod.sarvam_client, "detect_language", mock_detect), \
         patch.object(acs_mod.bhashini_client, "transcribe", mock_transcribe), \
         patch.object(audio_chat_service, "transcribe_audio", mock_whisper), \
         patch.object(acs_mod.chat_service, "generate_grounded_reply", mock_llm), \
         patch.object(audio_chat_service, "text_to_speech", mock_gtts):

        turn = await audio_chat_service.process_voice_turn_v2(audio_bytes=fake_audio)

        assert _telemetry["bhashini_language_gap"] == initial_gap_count + 1
        assert _telemetry["fallback_total"] == initial_fallback_total + 1
        assert turn["tier_used"] == "fallback"



def test_gate7_chat_health_endpoint():
    response = client.get("/api/v2/chat/health")
    assert response.status_code == 200
    data = response.json()

    assert "sarvam_configured" in data
    assert "bhashini_configured" in data
    assert "voice_tier" in data
    assert "telemetry" in data

    telemetry = data["telemetry"]
    assert "bhashini_total" in telemetry
    assert "bhashini_asr_gtts_tts_total" in telemetry
    assert "fallback_total" in telemetry
    assert "bhashini_language_gap" in telemetry
    assert "voice_degraded_language_notice_total" in telemetry


@pytest.mark.asyncio
async def test_gate7_voice_chat_api_endpoint():
    fake_audio_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 50

    mock_detect = AsyncMock(return_value="hi-IN")
    mock_transcribe = AsyncMock(return_value="डीपीआर कैसे डाउनलोड करें?")
    mock_synthesize = AsyncMock(return_value=b"SYNTHESIZED_WAV_BYTES")
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="डीपीआर डाउनलोड करने के लिए एक्सपोर्ट बटन दबाएं।",
        tool_call={"name": "export_dpr", "arguments": {"format": "pdf"}},
        model="openai/gpt-oss-20b",
        latency_ms=210.0,
        sources=["DPR Engine"],
    ))

    with patch.object(acs_mod.sarvam_client, "detect_language", mock_detect), \
         patch.object(acs_mod.bhashini_client, "transcribe", mock_transcribe), \
         patch.object(acs_mod.bhashini_client, "synthesize", mock_synthesize), \
         patch.object(acs_mod.chat_service, "generate_grounded_reply", mock_llm):

        files = {"file": ("test.wav", fake_audio_bytes, "audio/wav")}
        data = {
            "context": json.dumps({"currentTab": "dpr"}),
            "history": json.dumps([]),
        }

        response = client.post("/api/v2/chat/audio", files=files, data=data)
        assert response.status_code == 200
        res_json = response.json()

        assert res_json["tier_used"] == "bhashini"
        assert res_json["detected_language"] == "hi-IN"
        assert res_json["language"] == "hi"
        assert res_json["user_transcript"] == "डीपीआर कैसे डाउनलोड करें?"
        assert res_json["reply"] == "डीपीआर डाउनलोड करने के लिए एक्सपोर्ट बटन दबाएं।"
        assert res_json["tool_call"] == {"name": "export_dpr", "arguments": {"format": "pdf"}}
        assert res_json["audio_base64"].startswith("data:audio/wav;base64,")
        assert "timestamp" in res_json
