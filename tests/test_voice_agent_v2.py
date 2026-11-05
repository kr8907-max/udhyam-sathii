
from __future__ import annotations
import io
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.action_registry import ACTION_REGISTRY_SCHEMA, ACTION_NAMES
from backend.app.core.sarvam_client import (
    SarvamASRResult,
    SarvamTTSResult,
    SarvamUnavailableError,
)
from backend.app.core.audio_chat_service import (
    audio_chat_service,
    build_degraded_language_notice_response,
    normalize_lang,
)
from backend.app.core.chat_service import LLMReplyResult

client = TestClient(app)


def patch_all(target_attr, new_val):
    for mod_name in [
        "app.core.sarvam_client",
        "backend.app.core.sarvam_client",
        "app.core.audio_chat_service",
        "backend.app.core.audio_chat_service",
        "app.core.chat_service",
        "backend.app.core.chat_service",
        "app.routers.chat",
        "backend.app.routers.chat",
    ]:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            parts = target_attr.split(".")
            obj = mod
            for p in parts[:-1]:
                obj = getattr(obj, p, None)
                if obj is None:
                    break
            if obj is not None and hasattr(obj, parts[-1]):
                setattr(obj, parts[-1], new_val)



def test_action_registry_schema():
    expected_actions = {"navigate_to_tab", "change_language", "run_analysis", "export_dpr"}
    assert set(ACTION_NAMES) == expected_actions

    nav_tool = next(t for t in ACTION_REGISTRY_SCHEMA if t["function"]["name"] == "navigate_to_tab")
    tab_enum = nav_tool["function"]["parameters"]["properties"]["tab"]["enum"]
    assert "dashboard" in tab_enum
    assert "govt_schemes" in tab_enum
    assert "dpr" in tab_enum
    assert "risk_analysis" in tab_enum
    assert "business_plan" in tab_enum

    lang_tool = next(t for t in ACTION_REGISTRY_SCHEMA if t["function"]["name"] == "change_language")
    lang_enum = lang_tool["function"]["parameters"]["properties"]["language"]["enum"]
    assert set(lang_enum) == {"en", "hi", "mr", "ta", "te", "kn"}



def test_normalize_lang():
    assert normalize_lang("hi-IN") == "hi"
    assert normalize_lang("ta-IN") == "ta"
    assert normalize_lang("mr-IN") == "mr"
    assert normalize_lang("en-US") == "en"
    assert normalize_lang("hi") == "hi"



def test_degraded_language_notice():
    result = build_degraded_language_notice_response()
    assert result["tier_used"] == "fallback"
    assert "Voice support for this language isn't available" in result["reply"]
    assert "अभी इस भाषा में आवाज़ सहायता उपलब्ध नहीं है" in result["reply"]
    assert result["tool_call"] is None
    assert result["audio_base64"].startswith("data:audio/mp3;base64,")



@pytest.mark.asyncio
@pytest.mark.parametrize("lang_code,transcript,sample_reply", [
    ("hi-IN", "मुझे सरकारी योजनाएं दिखाओ", "यहाँ आपके लिए उपयुक्त सरकारी योजनाएं हैं।"),
    ("ta-IN", "திட்டங்களை காட்டு", "உங்களுக்கான அரசு திட்டங்கள் இங்கே உள்ளன."),
    ("mr-IN", "शासकीय योजना दाखवा", "येथे आपल्या व्यवसायासाठी शासकीय योजना आहेत."),
])
async def test_sarvam_happy_path_indic_languages(lang_code, transcript, sample_reply):
    fake_audio = b"\x00\x01\x02" * 100

    mock_detect = AsyncMock(return_value=lang_code)
    mock_asr = AsyncMock(return_value=transcript)
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text=sample_reply,
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=450.0,
        sources=["Udyam Schemes Engine"],
    ))
    mock_tts = AsyncMock(return_value=b"\xff\xfb\x90\x00" * 50)

    patch_all("sarvam_client.detect_language", mock_detect)
    patch_all("bhashini_client.transcribe", mock_asr)
    patch_all("chat_service.generate_grounded_reply", mock_llm)
    patch_all("bhashini_client.synthesize", mock_tts)

    turn_result = await audio_chat_service.process_voice_turn_v2(
        audio_bytes=fake_audio,
        context={"currentTab": "schemes"},
    )

    assert turn_result["tier_used"] in ("bhashini", "sarvam")
    assert turn_result["detected_language"] == lang_code
    assert turn_result["user_transcript"] == transcript
    assert turn_result["reply"] == sample_reply
    assert turn_result["tool_call"] is None
    assert turn_result["audio_base64"].startswith(("data:audio/mp3;base64,", "data:audio/wav;base64,"))

    mock_tts.assert_awaited_once()
    tts_args = mock_tts.call_args[0]
    assert tts_args[1] == lang_code



@pytest.mark.asyncio
async def test_forced_sarvam_failure_fallback():
    fake_audio = b"\x00\x01\x02" * 100

    mock_asr = AsyncMock(side_effect=SarvamUnavailableError("Sarvam service timed out (4000ms)"))
    mock_whisper = MagicMock(return_value={
        "transcript": "नमस्ते, क्या हाल है",
        "language": "hi",
        "detected_language_code": "hi",
        "language_name": "Hindi",
        "latency_s": 0.5,
    })
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="नमस्ते! मैं उद्यम साथी हूँ।",
        tool_call=None,
        model="openai/gpt-oss-20b",
        latency_ms=300.0,
        sources=[],
    ))
    mock_gtts = MagicMock(return_value={
        "audio_base64": "data:audio/mp3;base64,AAAA",
        "latency_s": 0.2,
    })

    patch_all("sarvam_client.detect_language", mock_asr)
    patch_all("sarvam_client.transcribe", mock_asr)
    patch_all("audio_chat_service.transcribe_audio", mock_whisper)
    patch_all("chat_service.generate_grounded_reply", mock_llm)
    patch_all("audio_chat_service.text_to_speech", mock_gtts)

    turn_result = await audio_chat_service.process_voice_turn_v2(
        audio_bytes=fake_audio,
    )

    assert turn_result["tier_used"] == "fallback"
    assert turn_result["detected_language"] == "hi"
    assert turn_result["reply"] == "नमस्ते! मैं उद्यम साथी हूँ।"
    mock_whisper.assert_called_once()
    mock_gtts.assert_called_once()



@pytest.mark.asyncio
async def test_sarvam_failure_unsupported_language_skips_llm():
    fake_audio = b"\x00\x01\x02" * 100

    mock_asr = AsyncMock(side_effect=SarvamUnavailableError("Sarvam 503 Service Unavailable"))
    mock_whisper = MagicMock(return_value={
        "transcript": "Some unsupported dialect speech",
        "language": "sd",
        "detected_language_code": "sd",
        "language_name": "Sindhi",
        "latency_s": 0.4,
    })
    mock_llm = MagicMock()

    patch_all("sarvam_client.detect_language", mock_asr)
    patch_all("sarvam_client.transcribe", mock_asr)
    patch_all("audio_chat_service.transcribe_audio", mock_whisper)
    patch_all("chat_service.generate_grounded_reply", mock_llm)

    turn_result = await audio_chat_service.process_voice_turn_v2(audio_bytes=fake_audio)

    mock_llm.assert_not_called()
    assert turn_result["tier_used"] == "fallback"
    assert "Voice support for this language isn't available" in turn_result["reply"]
    assert turn_result["tool_call"] is None



@pytest.mark.asyncio
@pytest.mark.parametrize("action_name,action_args,user_input", [
    ("navigate_to_tab", {"tab": "govt_schemes"}, "योजनाएं खोलो"),
    ("navigate_to_tab", {"tab": "dpr"}, "डीपीआर दिखाओ"),
    ("change_language", {"language": "hi"}, "हिंदी में बदलो"),
    ("run_analysis", {}, "विश्लेषण फिर से चलाओ"),
    ("export_dpr", {"format": "pdf"}, "डीपीआर पीडीएफ डाउनलोड करो"),
])
async def test_voice_turn_tool_calling_actions(action_name, action_args, user_input):
    fake_audio = b"\x00\x01\x02" * 100

    mock_detect = AsyncMock(return_value="hi-IN")
    mock_asr = AsyncMock(return_value=user_input)
    mock_llm = MagicMock(return_value=LLMReplyResult(
        text="आदेशानुसार कर दिया गया है।",
        tool_call={"name": action_name, "arguments": action_args},
        model="openai/gpt-oss-20b",
        latency_ms=300.0,
        sources=[],
    ))
    mock_tts = AsyncMock(return_value=b"\xff\xfb\x90\x00" * 30)

    patch_all("sarvam_client.detect_language", mock_detect)
    patch_all("bhashini_client.transcribe", mock_asr)
    patch_all("chat_service.generate_grounded_reply", mock_llm)
    patch_all("bhashini_client.synthesize", mock_tts)

    turn_result = await audio_chat_service.process_voice_turn_v2(audio_bytes=fake_audio)

    assert turn_result["tool_call"] is not None
    assert turn_result["tool_call"]["name"] == action_name
    assert turn_result["tool_call"]["arguments"] == action_args
    assert turn_result["reply"] == "आदेशानुसार कर दिया गया है।"
    assert turn_result["tier_used"] in ("bhashini", "sarvam")



def test_api_audio_endpoint_with_tool_call():
    mock_turn = AsyncMock(return_value={
        "user_transcript": "योजनाएं दिखाओ",
        "reply": "सरकारी योजनाएं खोली जा रही हैं।",
        "language": "hi",
        "language_name": "Hindi",
        "detected_language": "hi-IN",
        "tier_used": "sarvam",
        "sources": ["Udyam Schemes DB"],
        "audio_base64": "data:audio/mp3;base64,SUQzBAAAAAAA",
        "is_fallback": False,
        "model": "openai/gpt-oss-20b",
        "tool_call": {"name": "navigate_to_tab", "arguments": {"tab": "govt_schemes"}},
        "stt_latency_s": 0.25,
        "llm_latency_s": 0.35,
        "tts_latency_s": 0.15,
        "total_latency_s": 0.75,
    })

    patch_all("audio_chat_service.process_voice_turn_v2", mock_turn)

    fake_file = io.BytesIO(b"RIFF....WAVEfmt ....")
    response = client.post(
        "/api/v2/chat/audio",
        files={"file": ("test.wav", fake_file, "audio/wav")},
        data={"context": json.dumps({"activeTab": "dashboard"})},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_transcript"] == "योजनाएं दिखाओ"
    assert data["reply"] == "सरकारी योजनाएं खोली जा रही हैं।"
    assert data["detected_language"] == "hi-IN"
    assert data["tier_used"] == "sarvam"
    assert data["tool_call"] == {
        "name": "navigate_to_tab",
        "arguments": {"tab": "govt_schemes"},
    }
    assert data["audio_base64"].startswith("data:audio/mp3;base64,")
    assert data["total_latency_s"] == 0.75
