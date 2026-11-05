
from __future__ import annotations
import asyncio
import base64
import logging
import time
from typing import Optional, Any, Dict, Tuple

import httpx

try:
    from app.config import settings
except ImportError:
    from backend.app.config import settings

logger = logging.getLogger("udyam_saathi.bhashini")



class BhashiniLanguageUnsupportedError(Exception):
    pass


class BhashiniUnavailableError(Exception):
    pass



def normalize_bhashini_lang(lang_code: str) -> str:
    if not lang_code:
        return "en"
    return lang_code.split("-")[0].lower().strip()


_TTS_MAX_CHARS = 4000



class BhashiniClient:

    def __init__(self):
        self._config_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _get_timeout_seconds(self) -> float:
        ms = int(getattr(settings, "VOICE_CASCADE_TIMEOUT_MS", 4000))
        return ms / 1000.0

    def _get_tts_timeout_seconds(self) -> float:
        sec = float(getattr(settings, "VOICE_TTS_TIMEOUT_SECONDS", 25.0))
        return sec

    def _get_credentials(self) -> Tuple[str, str]:
        user_id = getattr(settings, "BHASHINI_USER_ID", "") or ""
        api_key = getattr(settings, "BHASHINI_API_KEY", "") or ""
        return user_id.strip(), api_key.strip()

    def _get_config_endpoint(self) -> str:
        return getattr(
            settings,
            "BHASHINI_CONFIG_ENDPOINT",
            "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline",
        )

    def _get_pipeline_id(self) -> str:
        return getattr(
            settings,
            "BHASHINI_PIPELINE_ID",
            "64392f96daac500b55c543cd",
        )

    def _get_cache_ttl(self) -> int:
        return int(getattr(settings, "BHASHINI_CONFIG_CACHE_TTL_SECONDS", 3600))

    async def _get_config(self, task_type: str, language_code: str) -> Dict[str, Any]:
        norm_lang = normalize_bhashini_lang(language_code)
        cache_key = (task_type.lower(), norm_lang)

        now = time.time()
        cached = self._config_cache.get(cache_key)
        if cached and cached.get("expires_at", 0) > now:
            return cached

        user_id, api_key = self._get_credentials()
        if not user_id or not api_key:
            raise BhashiniUnavailableError(
                "BHASHINI_USER_ID or BHASHINI_API_KEY is not configured"
            )

        endpoint = self._get_config_endpoint()
        pipeline_id = self._get_pipeline_id()
        timeout_s = self._get_timeout_seconds()

        request_payload = {
            "pipelineTasks": [
                {
                    "taskType": task_type.lower(),
                    "config": {
                        "language": {
                            "sourceLanguage": norm_lang,
                        }
                    },
                }
            ],
            "pipelineRequestConfig": {
                "pipelineId": pipeline_id,
            },
        }

        headers = {
            "userID": user_id,
            "ulcaApiKey": api_key,
            "Content-Type": "application/json",
        }

        logger.debug(
            "Fetching Bhashini pipeline config for task='%s', lang='%s' from ULCA...",
            task_type,
            norm_lang,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                res = await client.post(endpoint, json=request_payload, headers=headers)

            if res.status_code in (401, 403):
                raise BhashiniUnavailableError(
                    f"Bhashini ULCA authentication failed (HTTP {res.status_code})"
                )

            if res.status_code != 200:
                raise BhashiniUnavailableError(
                    f"Bhashini config discovery failed (HTTP {res.status_code}): {res.text[:200]}"
                )

            data = res.json()
            if not isinstance(data, dict):
                raise BhashiniUnavailableError("Malformed Bhashini config response format")

            pipeline_configs = data.get("pipelineResponseConfig", [])
            service_id = None
            for p_conf in pipeline_configs:
                if p_conf.get("taskType", "").lower() == task_type.lower():
                    cfg_list = p_conf.get("config", [])
                    if cfg_list and isinstance(cfg_list, list):
                        for item in cfg_list:
                            lang_info = item.get("language", {})
                            src_lang = (
                                lang_info.get("sourceLanguage")
                                or lang_info.get("targetLanguage")
                                or ""
                            ).lower().strip()
                            if src_lang == norm_lang:
                                service_id = item.get("serviceId")
                                break
                        if not service_id and len(cfg_list) == 1 and not cfg_list[0].get("language"):
                            service_id = cfg_list[0].get("serviceId")
                    break

            if not service_id:
                logger.warning(
                    "Bhashini language gap: no active %s service found for language '%s'",
                    task_type,
                    norm_lang,
                )
                raise BhashiniLanguageUnsupportedError(
                    f"Bhashini has no active {task_type} service for language '{norm_lang}' (language not supported)"
                )

            endpoint_info = data.get("pipelineInferenceAPIEndPoint", {})
            callback_url = endpoint_info.get("callbackUrl")
            api_key_info = endpoint_info.get("inferenceApiKey", {})
            inference_key = api_key_info.get("value")
            inference_key_name = api_key_info.get("name", "Authorization")

            if not callback_url or not inference_key:
                raise BhashiniUnavailableError(
                    "Bhashini endpoint metadata missing callbackUrl or inferenceApiKey"
                )

            resolved_config = {
                "serviceId": service_id,
                "callback_url": callback_url,
                "inference_key_name": inference_key_name,
                "inference_key": inference_key,
                "expires_at": now + self._get_cache_ttl(),
            }

            async with self._lock:
                self._config_cache[cache_key] = resolved_config

            logger.debug(
                "Bhashini config cached for (%s, %s): serviceId=%s",
                task_type,
                norm_lang,
                service_id,
            )

            return resolved_config

        except httpx.TimeoutException:
            raise BhashiniUnavailableError(
                f"Bhashini pipeline config timed out after {timeout_s}s"
            )
        except httpx.RequestError as e:
            raise BhashiniUnavailableError(f"Bhashini config connection error: {e}")
        except (BhashiniLanguageUnsupportedError, BhashiniUnavailableError):
            raise
        except Exception as e:
            logger.error("Unexpected error in Bhashini _get_config: %s", e, exc_info=True)
            raise BhashiniUnavailableError(f"Unexpected Bhashini error: {e}")

    async def transcribe(
        self,
        audio_bytes: bytes,
        language_code: str,
        audio_format: str = "wav",
        sampling_rate: int = 16000,
    ) -> str:
        if not audio_bytes:
            return ""

        norm_lang = normalize_bhashini_lang(language_code)
        config = await self._get_config("asr", norm_lang)

        timeout_s = self._get_timeout_seconds()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        payload = {
            "pipelineTasks": [
                {
                    "taskType": "asr",
                    "config": {
                        "language": {
                            "sourceLanguage": norm_lang,
                        },
                        "serviceId": config["serviceId"],
                        "audioFormat": audio_format,
                        "samplingRate": sampling_rate,
                    },
                }
            ],
            "inputData": {
                "audio": [
                    {
                        "audioContent": audio_b64,
                    }
                ]
            },
        }

        headers = {
            config["inference_key_name"]: config["inference_key"],
            "Content-Type": "application/json",
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                res = await client.post(config["callback_url"], json=payload, headers=headers)

            if res.status_code in (401, 403):
                raise BhashiniUnavailableError("Bhashini ASR inference authorization failed")

            if res.status_code != 200:
                raise BhashiniUnavailableError(
                    f"Bhashini ASR failed with status {res.status_code}: {res.text[:200]}"
                )

            data = res.json()
            pipeline_resp = data.get("pipelineResponse", [])
            transcript = ""
            if pipeline_resp and isinstance(pipeline_resp, list):
                output_list = pipeline_resp[0].get("output", [])
                if output_list and isinstance(output_list, list):
                    transcript = output_list[0].get("source", "")

            latency = round(time.perf_counter() - start, 3)
            logger.debug(
                "Bhashini ASR success: lang='%s', chars=%d, latency=%.3fs",
                norm_lang,
                len(transcript),
                latency,
            )
            return transcript.strip()

        except httpx.TimeoutException:
            raise BhashiniUnavailableError(f"Bhashini ASR timed out after {timeout_s}s")
        except httpx.RequestError as e:
            raise BhashiniUnavailableError(f"Bhashini ASR connection error: {e}")
        except BhashiniUnavailableError:
            raise
        except Exception as e:
            logger.error("Bhashini ASR unexpected error: %s", e, exc_info=True)
            raise BhashiniUnavailableError(f"Bhashini ASR error: {e}")

    async def synthesize(
        self,
        text: str,
        language_code: str,
        gender: str = "female",
        sampling_rate: int = 22050,
    ) -> bytes:
        if not text or not text.strip():
            return b""

        norm_lang = normalize_bhashini_lang(language_code)
        config = await self._get_config("tts", norm_lang)

        cleaned_text = text.strip()
        max_chars = int(getattr(settings, "TTS_MAX_CHARS", _TTS_MAX_CHARS))
        if len(cleaned_text) > max_chars:
            cleaned_text = cleaned_text[:max_chars]
            last_punc = max(cleaned_text.rfind("."), cleaned_text.rfind("।"), cleaned_text.rfind("?"))
            if last_punc > int(max_chars * 0.8):
                cleaned_text = cleaned_text[:last_punc + 1]

        timeout_s = self._get_tts_timeout_seconds()

        payload = {
            "pipelineTasks": [
                {
                    "taskType": "tts",
                    "config": {
                        "language": {
                            "sourceLanguage": norm_lang,
                        },
                        "serviceId": config["serviceId"],
                        "gender": gender,
                        "samplingRate": sampling_rate,
                    },
                }
            ],
            "inputData": {
                "input": [
                    {
                        "source": cleaned_text,
                    }
                ]
            },
        }

        headers = {
            config["inference_key_name"]: config["inference_key"],
            "Content-Type": "application/json",
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                res = await client.post(config["callback_url"], json=payload, headers=headers)

            if res.status_code in (401, 403):
                raise BhashiniUnavailableError("Bhashini TTS inference authorization failed")

            if res.status_code != 200:
                raise BhashiniUnavailableError(
                    f"Bhashini TTS failed with status {res.status_code}: {res.text[:200]}"
                )

            data = res.json()
            pipeline_resp = data.get("pipelineResponse", [])
            audio_b64 = ""
            if pipeline_resp and isinstance(pipeline_resp, list):
                audio_list = pipeline_resp[0].get("audio", [])
                if audio_list and isinstance(audio_list, list):
                    audio_b64 = audio_list[0].get("audioContent", "")

            if not audio_b64:
                raise BhashiniUnavailableError("Bhashini TTS returned empty audio payload")

            audio_bytes = base64.b64decode(audio_b64)
            latency = round(time.perf_counter() - start, 3)
            logger.debug(
                "Bhashini TTS success: lang='%s', bytes=%d, latency=%.3fs",
                norm_lang,
                len(audio_bytes),
                latency,
            )
            return audio_bytes

        except httpx.TimeoutException:
            raise BhashiniUnavailableError(f"Bhashini TTS timed out after {timeout_s}s")
        except httpx.RequestError as e:
            raise BhashiniUnavailableError(f"Bhashini TTS connection error: {e}")
        except BhashiniUnavailableError:
            raise
        except Exception as e:
            logger.error("Bhashini TTS unexpected error: %s", e, exc_info=True)
            raise BhashiniUnavailableError(f"Bhashini TTS error: {e}")

    async def prewarm_cache(self, language_codes: Optional[list[str]] = None) -> None:
        target_langs = language_codes or ["hi", "en", "mr", "bn", "ta", "te", "kn", "gu", "pa", "ur"]
        logger.info("Pre-warming Bhashini pipeline config cache for %d languages...", len(target_langs))

        tasks = []
        for lang in target_langs:
            tasks.append(self._prewarm_one("asr", lang))
            tasks.append(self._prewarm_one("tts", lang))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _prewarm_one(self, task_type: str, lang: str) -> None:
        try:
            await self._get_config(task_type, lang)
        except Exception as e:
            logger.debug("Pre-warm skipped for (%s, %s): %s", task_type, lang, e)


bhashini_client = BhashiniClient()
