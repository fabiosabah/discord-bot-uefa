# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import os
import re
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential, wait_random_exponential, retry_if_exception_type
from google.genai import errors
from PIL import Image
import io
from core.dota_heroes import resolve_hero_name

logger = logging.getLogger("OCR")


def can_process_ocr() -> bool:
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            import google.genai  # noqa: F401
            return True
        except ImportError:
            return False

    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("OPENAI_API_KEY"))


def can_process_llm() -> bool:
    return can_process_ocr()


def _build_ai_client():
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is required for GEMINI_API_KEY. Install it with `pip install google-genai`."
            ) from exc

        client = genai.Client(api_key=gemini_key)
        return "gemini", client

    try:
        import openai
    except ImportError as exc:
        raise RuntimeError(
            "openai is required if GEMINI_API_KEY is not set. Install it with `pip install openai`."
        ) from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OCR processing.")

    openai.api_key = api_key
    api_base = os.getenv("OPENAI_API_BASE")
    if api_base:
        openai.api_base = api_base

    return "openai", openai


def _normalize_team(team_value: str | None) -> str | None:
    if team_value is None:
        return None

    normalized = str(team_value).strip().lower()
    if not normalized:
        return None

    if "radiant" in normalized or "esquerda" in normalized or "left" in normalized:
        return "radiant"
    if "dire" in normalized or "direita" in normalized or "right" in normalized:
        return "dire"

    if normalized in {"r", "rad", "radiante", "radiancia", "radiância"}:
        return "radiant"
    if normalized in {"d", "dir", "direção", "direccao"}:
        return "dire"

    # Só aceitar "radiant" ou "dire" - rejeitar qualquer outro valor
    return None


def generate_match_hash(parsed: dict[str, Any]) -> str:
    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    duration = str(match_info.get("duration") or "").strip()
    score = match_info.get("score") or {}
    radiant_score = score.get("radiant")
    dire_score = score.get("dire")
    players = parsed.get("players_data") or parsed.get("players") or []

    entries: list[str] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        player_name = (player.get("player_name") or player.get("name") or player.get("player") or "").strip()
        kda = str(player.get("kda") or player.get("score") or "").strip()
        entries.append(f"{player_name}:{kda}")

    entries = sorted(entries)
    canonical = {
        "duration": duration,
        "radiant_score": radiant_score,
        "dire_score": dire_score,
        "players": entries,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_text_from_response(response: Any) -> str:
    if response is None:
        return ""

    if hasattr(response, "output") and response.output:
        for item in response.output:
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for part in content:
                    text = getattr(part, "text", None)
                    if isinstance(text, str) and text:
                        return text
            text = getattr(item, "text", None)
            if isinstance(text, str) and text:
                return text

    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text

    if hasattr(response, "to_dict"):
        try:
            data = response.to_dict()
            if isinstance(data, dict):
                output = data.get("output")
                if isinstance(output, list):
                    for item in output:
                        content = item.get("content")
                        if isinstance(content, list):
                            for part in content:
                                text = part.get("text")
                                if isinstance(text, str) and text:
                                    return text
                candidates = data.get("candidates")
                if isinstance(candidates, list):
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        content = candidate.get("content")
                        if isinstance(content, dict):
                            content = [content]
                        if isinstance(content, list):
                            candidate_texts = []
                            for part in content:
                                if not isinstance(part, dict):
                                    continue
                                if part.get("role") == "model":
                                    text = part.get("text")
                                    if isinstance(text, str) and text:
                                        candidate_texts.append(text)
                                elif not part.get("thought", False):
                                    text = part.get("text")
                                    if isinstance(text, str) and text:
                                        candidate_texts.append(text)
                            if candidate_texts:
                                return "\n".join(candidate_texts).strip()
                        text = candidate.get("text")
                        if isinstance(text, str) and text:
                            return text
        except Exception:
            pass

    return ""


def _is_rate_limit_exception(exc: Exception) -> bool:
    if exc is None:
        return False
    if getattr(exc, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return any(keyword in message for keyword in ("429", "too many requests", "resource_exhausted", "rate limit"))


def extract_text_from_image_url(image_url: str) -> str:
    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-3-flash-preview"
    instructions = (
        "Você é um assistente especializado em Dota 2."
        "Não adicione explicações, marcações ou comentários. Retorne apenas o texto bruto sem interpretação adicional."
    )

    if provider == "gemini":
        from google.genai import types
        # Configuração de Thinking conforme o Google AI Studio
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
            ),
        )
        
        mime_type = "image/jpeg" if image_url.lower().endswith((".jpg", ".jpeg")) else "image/png"

        def build_image_part(image_url: str):
            # Estratégia 1: Tentar passar a URL direto (sem baixar)
            try:
                return types.Part.from_uri(uri=image_url, mime_type=mime_type)
            except TypeError:
                pass
            
            # Estratégia 2: Tentar file_uri
            try:
                return types.Part.from_uri(file_uri=image_url, mime_type=mime_type)
            except TypeError:
                pass
            
            # Fallback 3: Se falhar, baixar a imagem com headers corretos
            logger.info(f"Recaindo para download da imagem: {image_url}")
            import requests

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            response = requests.get(image_url, headers=headers, timeout=30)
            response.raise_for_status()
            image_data = response.content
            return types.Part.from_bytes(data=image_data, mime_type=mime_type)

        from google.genai.errors import ClientError

        def _is_model_not_found_error(exc: Exception) -> bool:
            if isinstance(exc, ClientError):
                if getattr(exc, "status_code", None) == 404:
                    return True
            message = str(exc).lower()
            return "not found" in message or "not supported for generatecontent" in message

        image_part = build_image_part(image_url)

        @retry(
            retry=retry_if_exception(_is_rate_limit_exception),
            wait=wait_random_exponential(multiplier=8, min=15, max=180),
            stop=stop_after_attempt(8),
            reraise=True,
        )
        def generate_content_with_retry(model_name: str):
            return client.models.generate_content(
                model=model_name,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=instructions),
                            image_part
                        ],
                    ),
                ],
                config=generate_content_config
            )

        candidate_models = [model]
        if model in {"gemini-1.5-flash", "gemini-1.5-flash-preview", "gemini-1.5-flash-002", "gemini-1.5-pro"}:
            candidate_models.extend([
                "gemini-3-flash-preview",
                "gemini-3-flash",
                "gemini-3-flash-002",
            ])
        elif model in {"gemini-3-flash-preview", "gemini-3-flash", "gemini-3-flash-002"}:
            candidate_models.extend([
                "gemini-3-flash-preview",
                "gemini-3-flash",
                "gemini-3-flash-002",
                "gemini-1.5-flash",
            ])
        else:
            candidate_models.extend([
                "gemini-3-flash-preview",
                "gemini-3-flash",
                "gemini-3-flash-002",
            ])

        response = None
        last_error: Exception | None = None
        for candidate in candidate_models:
            try:
                if candidate != model:
                    logger.warning(f"Modelo {model} não disponível, tentando fallback {candidate}.")
                response = generate_content_with_retry(candidate)
                if hasattr(response, "to_dict"):
                    try:
                        raw_response = response.to_dict()
                        logger.info("OCR AI raw response: %s", json.dumps(raw_response, ensure_ascii=False))
                    except Exception:
                        logger.info("OCR AI raw response: %s", str(response))
                else:
                    logger.info("OCR AI raw response: %s", str(response))

                text = _extract_text_from_response(response)
                if not text:
                    text = getattr(response, "text", "")

                break
            except Exception as exc:
                last_error = exc
                if _is_model_not_found_error(exc) and candidate != candidate_models[-1]:
                    continue
                raise

        if response is None:
            raise RuntimeError(
                "Falha ao encontrar um modelo Gemini disponível para OCR."
            ) from last_error
    else:
        # Fallback para OpenAI se configurado
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instructions},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        text = response.choices[0].message.content

    if not text:
        raise RuntimeError("Falha ao extrair texto da imagem via OCR.")
    return text


def extract_text_from_image_data(image_data: bytes) -> str:
    logger.info(f"[OCR] Starting text extraction from image data ({len(image_data)} bytes)")
    
    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-3-flash-preview"
    instructions = (
        "Você é um assistente especializado em Dota 2."
        "Não adicione explicações, marcações ou comentários. Retorne apenas o texto bruto sem interpretação adicional."
    )

    logger.debug(f"[OCR] Using {provider} provider with model {model} for text extraction")

    if provider == "gemini":
        from google.genai import types
        # Configuração de Thinking conforme o Google AI Studio
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
            ),
        )
        
        mime_type = "image/png"  # Default to PNG
        
        image_part = types.Part.from_bytes(data=image_data, mime_type=mime_type)
        logger.debug(f"[OCR] Created image part for Gemini API with mime_type {mime_type}")

        from google.genai.errors import ClientError

        def _is_rate_limit_exception(exc: Exception) -> bool:
            if isinstance(exc, ClientError):
                if getattr(exc, "status_code", None) == 429:
                    return True
            message = str(exc).lower()
            return "rate limit" in message or "quota exceeded" in message

        def _is_model_not_found_error(exc: Exception) -> bool:
            if isinstance(exc, ClientError):
                if getattr(exc, "status_code", None) == 404:
                    return True
            message = str(exc).lower()
            return "not found" in message or "not supported for generatecontent" in message

        @retry(
            retry=retry_if_exception(_is_rate_limit_exception),
            wait=wait_random_exponential(multiplier=8, min=15, max=180),
            stop=stop_after_attempt(8),
            reraise=True,
        )
        def generate_content_with_retry(model_name: str):
            logger.debug(f"[OCR] Calling Gemini API for text extraction with model {model_name}")
            return client.models.generate_content(
                model=model_name,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=instructions),
                            image_part
                        ],
                    ),
                ],
                config=generate_content_config
            )

        candidate_models = [model]
        if model in {"gemini-1.5-flash", "gemini-1.5-flash-preview", "gemini-1.5-flash-002", "gemini-1.5-pro"}:
            candidate_models.extend([
                "gemini-3-flash-preview",
                "gemini-3-flash",
                "gemini-3-flash-002",
            ])
        elif model in {"gemini-3-flash-preview", "gemini-3-flash", "gemini-3-flash-002"}:
            candidate_models.extend([
                "gemini-3-flash-preview",
                "gemini-3-flash",
                "gemini-3-flash-002",
                "gemini-1.5-flash",
                "gemini-1.5-flash-preview",
                "gemini-1.5-flash-002",
                "gemini-1.5-pro",
            ])

        logger.debug(f"[OCR] Trying candidate models: {candidate_models}")
        response = None
        last_error = None
        for candidate in candidate_models:
            try:
                response = generate_content_with_retry(candidate)
                if response and hasattr(response, "text"):
                    text = response.text
                elif response and hasattr(response, "candidates"):
                    candidate_obj = response.candidates[0] if response.candidates else None
                    if candidate_obj and hasattr(candidate_obj, "content"):
                        text = candidate_obj.content.parts[0].text if candidate_obj.content.parts else ""
                    else:
                        text = ""
                else:
                    text = ""

                if not text:
                    try:
                        text = _extract_text_from_response(response)
                        logger.debug(f"[OCR] Extracted text using fallback method: {len(text)} characters")
                    except Exception:
                        logger.info("OCR AI raw response: %s", str(response))
                else:
                    logger.info("OCR AI raw response: %s", str(response))

                logger.info(f"[OCR] Successfully extracted text using model {candidate}: {len(text)} characters")
                break
            except Exception as exc:
                last_error = exc
                logger.warning(f"[OCR] Failed with model {candidate}: {exc}")
                if _is_model_not_found_error(exc) and candidate != candidate_models[-1]:
                    continue
                raise

        if response is None:
            logger.error("[OCR] Failed to find available Gemini model for OCR")
            raise RuntimeError(
                "Falha ao encontrar um modelo Gemini disponível para OCR."
            ) from last_error
    else:
        # For OpenAI, we need to encode the image data as base64
        logger.debug("[OCR] Using OpenAI for text extraction")
        import base64
        image_b64 = base64.b64encode(image_data).decode('utf-8')
        image_url = f"data:image/png;base64,{image_b64}"
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instructions},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        text = response.choices[0].message.content
        logger.info(f"[OCR] OpenAI text extraction completed: {len(text)} characters")

    if not text:
        logger.error("[OCR] No text extracted from image")
        raise RuntimeError("Falha ao extrair texto da imagem via OCR.")
    
    logger.info(f"[OCR] Text extraction completed successfully: {len(text)} characters")
    return text


def _parse_duration(text: str) -> str | None:
    match = re.search(r"(\d{1,2}:\d{2})", text)
    return match.group(1) if match else None


def _parse_team_score(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"radiant_score": None, "dire_score": None, "radiant_win": None}
    score_match = re.search(r"(?i)(radiant|dire)\s*(\d+)\s*[-–]\s*(\d+)\s*(radiant|dire)?", text)
    if score_match:
        label_a = score_match.group(1).lower()
        score_a = int(score_match.group(2))
        score_b = int(score_match.group(3))
        label_b = score_match.group(4).lower() if score_match.group(4) else None
        if label_a == "radiant":
            result["radiant_score"] = score_a
            result["dire_score"] = score_b
        else:
            result["radiant_score"] = score_b
            result["dire_score"] = score_a
        if label_b:
            result["radiant_win"] = label_b == "radiant"
    elif re.search(r"(?i)radiant victory", text):
        result["radiant_win"] = True
    elif re.search(r"(?i)dire victory", text):
        result["radiant_win"] = False
    return result


def _parse_kills(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"radiant_kills": None, "dire_kills": None}
    if "kills" in text.lower():
        kills = re.findall(r"(\d+)\s*[kK]ills", text)
        if len(kills) >= 2:
            result["radiant_kills"] = int(kills[0])
            result["dire_kills"] = int(kills[1])
    return result


def _parse_gold(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"radiant_gold": None, "dire_gold": None}
    gold_matches = re.findall(r"(\d{1,3}(?:[.,]\d{3})*)\s*(?:gold|net worth)", text, flags=re.IGNORECASE)
    if len(gold_matches) >= 2:
        try:
            result["radiant_gold"] = int(gold_matches[0].replace(".", "").replace(",", ""))
            result["dire_gold"] = int(gold_matches[1].replace(".", "").replace(",", ""))
        except ValueError:
            pass
    return result


def _normalize_player_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return value
    return re.sub(r"\s*\[[^\]]+\]\s*$", "", value).strip()


def _parse_players(text: str) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pattern = re.compile(
        r"^(?P<name>.+?)\s+(?P<score>\d+\s*[/x]\s*\d+\s*[/x]\s*\d+|\d+)\s+(?P<gold>\d{1,3}(?:[.,]\d{3})*)(?:\s+gold)?(?:\s+(?P<hero>.+))?$",
        re.IGNORECASE
    )
    for line in lines:
        match = pattern.search(line)
        if match:
            name = _normalize_player_name(match.group("name"))
            score = match.group("score")
            gold_text = match.group("gold")
            hero = match.group("hero")
            try:
                gold = int(gold_text.replace(".", "").replace(",", ""))
            except ValueError:
                gold = None
            hero_value = hero.strip() if isinstance(hero, str) and hero.strip() else None
            if hero_value:
                resolved_hero, _, _ = resolve_hero_name(hero_value)
                hero_value = resolved_hero
            players.append({
                "name": name,
                "score": score,
                "gold": gold,
                "hero": hero_value,
                "raw_line": line
            })
    return players


def _is_probably_dota_score_text(raw_text: str) -> bool:
    if not raw_text:
        return False

    text = raw_text.lower()
    keywords = [
        "radiant",
        "dire",
        "kda",
        "net worth",
        "networth",
        "gold",
        "hero",
        "kills",
        "score",
        "captains mode",
        "all pick",
        "ranked",
        "match duration",
        "duration",
    ]
    matches = sum(1 for keyword in keywords if keyword in text)
    if matches >= 2:
        return True

    if re.search(r"\b\d+\s*[/x]\s*\d+\s*[/x]\s*\d+\b", raw_text):
        return True

    return False


def _parse_json_payload(raw_text: str) -> dict[str, Any] | None:
    candidate = raw_text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = candidate[start:end + 1]

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    original_payload = payload
    if isinstance(payload.get("metadata_payload"), dict):
        payload = payload["metadata_payload"]

    if payload.get("valid_dota_screenshot") is False:
        return {
            "raw_text": raw_text,
            "valid_dota_screenshot": False,
            "metadata_payload": original_payload,
        }

    match_info = payload.get("match_info") or payload.get("game_details")
    players_data = payload.get("players_data")
    if isinstance(match_info, dict) and isinstance(players_data, list):
        parsed_players: list[dict[str, Any]] = []
        for entry in players_data:
            if not isinstance(entry, dict):
                continue
            hero_name = entry.get("hero_name") or entry.get("hero") or entry.get("heroi")
            if isinstance(hero_name, str):
                resolved_hero, _, _ = resolve_hero_name(hero_name)
                hero_name = resolved_hero
            else:
                hero_name = None

            parsed_players.append({
                "slot": entry.get("slot"),
                "player_name": _normalize_player_name(entry.get("player_name") or entry.get("name") or entry.get("player")),
                "hero_name": hero_name,
                "kills": entry.get("kills"),
                "deaths": entry.get("deaths"),
                "assists": entry.get("assists"),
                "networth": entry.get("networth") or entry.get("net_worth"),
                "team": _normalize_team(entry.get("team") or entry.get("side")),
                "raw_entry": entry,
            })

        score = match_info.get("score") or {}
        radiant_score = score.get("radiant")
        dire_score = score.get("dire")

        return {
            "raw_text": raw_text,
            "valid_dota_screenshot": True,
            "match_info": {
                "winner_team": _normalize_team(match_info.get("winner_team") or match_info.get("winner")),
                "duration": match_info.get("duration"),
                "datetime": match_info.get("datetime") or match_info.get("match_date"),
                "match_id": match_info.get("match_id"),
                "score": {
                    "radiant": radiant_score,
                    "dire": dire_score,
                },
            },
            "players_data": parsed_players,
            "metadata_payload": original_payload,
        }

    teams = payload.get("teams")
    if isinstance(match_info, dict) and isinstance(teams, dict):
        radiant_score = None
        dire_score = None
        score = match_info.get("score") or {}
        if isinstance(score, dict):
            radiant_score = score.get("radiant")
            dire_score = score.get("dire")

        winner = match_info.get("winner")
        radiant_win = isinstance(winner, str) and winner.lower() == "radiant"
        parsed_players: list[dict[str, Any]] = []

        for team_name in ("radiant", "dire"):
            team_list = teams.get(team_name) or []
            for entry in team_list:
                if not isinstance(entry, dict):
                    continue
                hero_name = entry.get("hero") or entry.get("hero_name") or entry.get("heroi")
                if isinstance(hero_name, str):
                    resolved_hero, _, _ = resolve_hero_name(hero_name)
                    hero_name = resolved_hero
                else:
                    hero_name = None

                parsed_players.append({
                    "name": _normalize_player_name(entry.get("player") or entry.get("name")),
                    "hero": hero_name,
                    "kills": entry.get("kills"),
                    "deaths": entry.get("deaths"),
                    "assists": entry.get("assists"),
                    "net_worth": entry.get("net_worth"),
                    "team": _normalize_team(team_name),
                    "raw_entry": entry,
                })

        return {
            "raw_text": raw_text,
            "steam_match_id": payload.get("steam_match_id"),
            "dota_match_id": payload.get("dota_match_id"),
            "match_date": payload.get("match_date") or match_info.get("date"),
            "mode": match_info.get("game_mode") or match_info.get("mode"),
            "winner": winner,
            "winner_team": _normalize_team(winner) if winner is not None else ("radiant" if radiant_win else "dire") if isinstance(radiant_win, bool) else None,
            "duration": match_info.get("duration"),
            "radiant_win": radiant_win,
            "radiant_score": radiant_score,
            "dire_score": dire_score,
            "score": score,
            "radiant_kills": None,
            "dire_kills": None,
            "radiant_gold": None,
            "dire_gold": None,
            "players": parsed_players,
            "metadata_payload": payload,
        }

    if "players" in payload or "score" in payload or "winner" in payload:
        radiant_score = None
        dire_score = None
        score = payload.get("score") or {}
        if isinstance(score, dict):
            radiant_score = score.get("radiant")
            dire_score = score.get("dire")

        radiant_win = payload.get("radiant_win")
        if not isinstance(radiant_win, bool):
            radiant_win = None

        parsed_players: list[dict[str, Any]] = []
        for entry in payload.get("players", []):
            if not isinstance(entry, dict):
                continue
            hero_name = entry.get("hero")
            if isinstance(hero_name, str):
                resolved_hero, _, _ = resolve_hero_name(hero_name)
                hero_name = resolved_hero
            else:
                hero_name = None
            parsed_players.append({
                "name": _normalize_player_name(entry.get("name")),
                "hero": hero_name,
                "score": entry.get("score"),
                "net_worth": entry.get("net_worth"),
                "team": _normalize_team(entry.get("team")),
                "raw_entry": entry,
            })

        return {
            "raw_text": raw_text,
            "steam_match_id": payload.get("steam_match_id"),
            "dota_match_id": payload.get("dota_match_id"),
            "match_date": payload.get("match_date"),
            "mode": payload.get("mode"),
            "winner": payload.get("winner"),
            "duration": payload.get("duration"),
            "radiant_win": radiant_win,
            "radiant_score": radiant_score,
            "dire_score": dire_score,
            "score": score,
            "radiant_kills": payload.get("radiant_kills"),
            "dire_kills": payload.get("dire_kills"),
            "radiant_gold": payload.get("radiant_gold"),
            "dire_gold": payload.get("dire_gold"),
            "players": parsed_players,
            "metadata_payload": payload,
        }

    return None


def _build_llm_prompt(raw_text: str | None = None, image_url: str | None = None) -> str:
    prompt = (
        "Você é uma Vision-LLM especialista em Dota 2. Sua tarefa é converter a imagem de um placar de partida em um objeto JSON estruturado para análise estatística.\n\n"
        "Siga estas etapas rigorosamente:\n\n"
        "1) IDENTIFICAÇÃO VISUAL DOS HERÓIS (Prioridade):\n"
        "   - Divida a imagem mentalmente em 10 colunas verticais.\n"
        "   - Identifique os 10 heróis por suas características visuais (silhueta, cores, rosto), ignorando itens cosméticos (skins).\n"
        "   - As 5 colunas da esquerda são \"radiant\" e as 5 da direita são \"dire\".\n"
        "   - Use o nome oficial do herói em INGLÊS (ex: \"Witch Doctor\", não \"Feiticeiro\"). Nunca retorne \"null\" para hero_name.\n"
        "   - DIFERENCIAÇÃO POR ATRIBUTOS FIXOS:\n"
        "       * ALCHEMIST: velhinho montado no pescoço de um humanoide um pouco barrigudo.\n"
        "       * GYROCOPTER: personagem operando um veículo mecânico voador. Não confundir com Sniper.\n"
        "       * LION: mão esquerda deformada (garra demoníaca) e rosto parecido com um felino.\n"
        "       * PANGOLIER: silhueta de tatu/pangolim, uso de chapéu de mosqueteiro e florete.\n"
        "       * SLARDAR: anatomia de criatura marinha/serpente. Não confundir com Naga Siren.\n"
        "       * SNIPER: anao(kneen) sempre carrega uma arma longa que se assemelha a um rifle.\n"
        "       * VENOMANCER: criatura insetoide/serpentina, tem skin que parece mecanica.\n"
        "       * WITCH DOCTOR: postura muito curvada, aparência baseada na cultura africana, geralmente cor roxa. Não confundir com Shadow Shaman.\n"
        "   - Se um herói for visualmente ambíguo devido a uma skin, procure por sua arma principal ou anatomia básica.\n"
        "   - Skins podem alterar cores, mas não devem mudar a identificação do herói.\n\n"
        "2) EXTRAÇÃO E DECOMPOSIÇÃO DE DADOS (OCR):\n"
        "   - Mapeie cada coluna de herói aos seus dados: player_name (topo), networth (número amarelo) e KDA (números na base).\n"
        "   - DECOMPOSIÇÃO DO KDA: Separe o formato \"Abates / Mortes / Assistências\" em três chaves inteiras distintas: \"kills\", \"deaths\" e \"assists\".\n"
        "   - Ignore o nível de maestria (número dentro do diamante).\n"
        "   - Capture as informações da partida (vencedor, duração, modo e placar total).\n\n"
        "3) REGRAS DE SAÍDA:\n"
        "   - Se a imagem não for um placar de Dota 2, retorne: {\"valid_dota_screenshot\": false}.\n"
        "   - Retorne APENAS o JSON puro. Não use blocos de código markdown (```json), explicações ou introduções.\n\n"
        "Estrutura do JSON:\n"
        "{\n"
        "  \"valid_dota_screenshot\": true,\n"
        "  \"match_info\": {\n"
        "    \"game_mode\": \"string\",\n"
        "    \"duration\": \"string (MM:SS)\",\n"
        "    \"winner\": \"Radiant ou Dire\",\n"
        "    \"score\": {\"radiant\": int, \"dire\": int}\n"
        "  },\n"
        "  \"players_data\": [\n"
        "    {\n"
        "      \"slot\": int (1-10),\n"
        "      \"team\": \"radiant ou dire\",\n"
        "      \"hero_name\": \"string\",\n"
        "      \"player_name\": \"string\",\n"
        "      \"networth\": int,\n"
        "      \"kills\": int,\n"
        "      \"deaths\": int,\n"
        "      \"assists\": int\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    if image_url:
        prompt += f"\nImagem de origem: {image_url}."

    if raw_text:
        prompt += f"\nTexto OCR:\n{raw_text}"
    return prompt


def _build_image_llm_prompt(image_url: str) -> str:
    prompt = _build_llm_prompt(None, image_url)
    prompt += (
        "\n\nUse apenas a imagem para extrair os dados da partida e retorne APENAS JSON válido com a estrutura esperada. "
        "Não inclua explicações, nem texto adicional."
    )
    return prompt


def _call_gemini_with_image(client, model: str, prompt: str, image_data: bytes, resize: bool = False) -> str:
    """Internal function to call Gemini API with image data. Decorated with retry logic."""
    from google.genai import types
    from PIL import Image
    import io

    logger.debug(f"[OCR] Preparing Gemini API call with model {model}, resize={resize}, image_size={len(image_data)} bytes")

    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
        ),
    )

    mime_type = "image/png"  # Default to PNG
    
    # Resize if needed (only on retry after server error)
    if resize:
        logger.debug("[OCR] Resizing image for API call")
        image = Image.open(io.BytesIO(image_data))
        max_width = 1280
        if image.width > max_width:
            aspect_ratio = image.height / image.width
            new_height = int(max_width * aspect_ratio)
            image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
            # Save to bytes
            output = io.BytesIO()
            image.save(output, format=image.format or 'PNG')
            image_data = output.getvalue()
            logger.info(f"[OCR] Image resized from {image.width}px to 1280px ({len(image_data)} bytes) to handle high server load")

    image_part = types.Part.from_bytes(data=image_data, mime_type=mime_type)
    logger.debug(f"[OCR] Created image part with mime_type {mime_type}")

    logger.debug(f"[OCR] Calling Gemini API with model {model}")
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                    image_part,
                ],
            ),
        ],
        config=generate_content_config
    )
    
    content = response.text
    logger.debug(f"[OCR] Gemini API call completed, received {len(content)} characters")
    return content


def _should_retry_llm_exception(exc: Exception) -> bool:
    """Check if an exception should trigger a retry for LLM calls."""
    if isinstance(exc, errors.ClientError):
        status_code = getattr(exc, "status_code", None)
        # Don't retry on 403 Forbidden (expired URLs) or 404 Not Found
        if status_code in (403, 404):
            logger.warning(f"[OCR] Not retrying LLM call due to permanent error: HTTP {status_code}")
            return False
        logger.debug(f"[OCR] Retrying LLM call due to client error: HTTP {status_code}")
        return True
    if isinstance(exc, errors.ServerError):
        logger.debug(f"[OCR] Retrying LLM call due to server error: {exc}")
        return True
    logger.debug(f"[OCR] Not retrying LLM call due to unknown error type: {type(exc).__name__}")
    return False


@retry(
    wait=wait_random_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception(_should_retry_llm_exception),
    reraise=True
)
def _parse_image_with_llm(image_data: bytes) -> dict[str, Any] | None:
    logger.debug(f"[OCR] Starting LLM image parsing with {len(image_data)} bytes of image data")
    
    if not can_process_llm():
        logger.warning("[OCR] LLM processing not available (missing API keys)")
        return None

    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-3-flash-preview"
    prompt = _build_image_llm_prompt("")
    
    logger.info(f"[OCR] Using {provider} provider with model {model} for image analysis")

    if provider == "gemini":
        try:
            # First attempt with original image (no resizing)
            logger.debug("[OCR] Calling Gemini API with original image")
            content = _call_gemini_with_image(client, model, prompt, image_data, resize=False)
            logger.debug(f"[OCR] Gemini API returned {len(content)} characters of content")
        except errors.ServerError as e:
            # On 503 Service Unavailable, retry with resized image
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                logger.info(f"[OCR] 503 Server error detected, retrying with resized image")
                try:
                    content = _call_gemini_with_image(client, model, prompt, image_data, resize=True)
                    logger.debug(f"[OCR] Gemini API retry returned {len(content)} characters of content")
                except Exception as retry_error:
                    logger.error(f"[OCR] Failed even with resized image: {retry_error}")
                    raise
            else:
                logger.error(f"[OCR] Gemini API server error: {e}")
                raise
    else:
        logger.warning(f"[OCR] Unsupported LLM provider: {provider}")
        return None

    logger.debug("[OCR] Parsing JSON payload from LLM response")
    parsed = _parse_json_payload(content)
    if parsed is not None:
        logger.info("[OCR] Successfully parsed match data from LLM response")
        return parsed
    else:
        logger.warning("[OCR] Failed to parse JSON payload from LLM response")
        return None


def _parse_text_with_llm(raw_text: str, image_url: str | None = None) -> dict[str, Any] | None:
    if not can_process_llm():
        return None

    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-3-flash-preview"
    prompt = _build_llm_prompt(raw_text, image_url)

    if provider == "gemini":
        from google.genai import types
        # Configuração de Thinking conforme o Google AI Studio
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
            ),
        )
        
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)],
                ),
            ],
            config=generate_content_config
        )
        content = response.text
    else:
        response = client.responses.create(
            model=model,
            input=raw_text,
            instructions=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "dota_match_data",
                    "description": "Estrutura JSON com match_info e teams para partida ",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "steam_match_id": {"type": ["string", "null"]},
                            "dota_match_id": {"type": ["string", "null"]},
                            "match_date": {"type": ["string", "null"]},
                            "valid_dota_screenshot": {"type": ["boolean", "null"]},
                            "match_info": {
                                "type": ["object", "null"],
                                "properties": {
                                    "game_mode": {"type": ["string", "null"]},
                                    "duration": {"type": ["string", "null"]},
                                    "winner": {"type": ["string", "null"]},
                                    "score": {
                                        "type": ["object", "null"],
                                        "properties": {
                                            "radiant": {"type": ["integer", "null"]},
                                            "dire": {"type": ["integer", "null"]}
                                        },
                                        "additionalProperties": False
                                    }
                                },
                                "additionalProperties": False
                            },
                            "teams": {
                                "type": ["object", "null"],
                                "properties": {
                                    "radiant": {
                                        "type": ["array", "null"],
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "player": {"type": ["string", "null"]},
                                                "hero": {"type": ["string", "null"]},
                                                "net_worth": {"type": ["integer", "null"]}
                                            },
                                            "required": ["player", "hero", "net_worth"],
                                            "additionalProperties": False
                                        }
                                    },
                                    "dire": {
                                        "type": ["array", "null"],
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "player": {"type": ["string", "null"]},
                                                "hero": {"type": ["string", "null"]},
                                                "net_worth": {"type": ["integer", "null"]}
                                            },
                                            "required": ["player", "hero", "net_worth"],
                                            "additionalProperties": False
                                        }
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "additionalProperties": False
                    }
                }
            },
            temperature=0.0,
            max_output_tokens=800,
        )

        content = _extract_text_from_response(response)

    parsed = _parse_json_payload(content)
    if parsed is not None:
        return parsed

    return _parse_json_payload(str(content) if content is not None else "")


def parse_dota_match_text(raw_text: str, image_url: str | None = None) -> dict[str, Any]:
    def _remove_kda_fields(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _remove_kda_fields(v)
                for k, v in value.items()
                if k != "kda"
            }
        if isinstance(value, list):
            return [_remove_kda_fields(item) for item in value]
        return value

    parsed = _parse_json_payload(raw_text)
    if parsed is not None:
        return _remove_kda_fields(parsed)

    if not _is_probably_dota_score_text(raw_text):
        return {
            "raw_text": raw_text,
            "valid_dota_screenshot": False,
            "reason": "text_does_not_match_dota_score",
        }

    parsed = _parse_text_with_llm(raw_text, image_url)
    if parsed is not None:
        return _remove_kda_fields(parsed)

    meta: dict[str, Any] = {
        "raw_text": raw_text,
        "duration": _parse_duration(raw_text),
        "radiant_win": None,
        "radiant_score": None,
        "dire_score": None,
        "radiant_kills": None,
        "dire_kills": None,
        "radiant_gold": None,
        "dire_gold": None,
        "players": [],
    }

    meta.update(_parse_team_score(raw_text))
    meta.update(_parse_kills(raw_text))
    meta.update(_parse_gold(raw_text))
    meta["players"] = _parse_players(raw_text)
    return meta


def process_match_screenshot(job_id: int, job: dict | None = None) -> dict[str, Any]:
    from core.db.ocr_repo import get_match_screenshot, set_match_screenshot_status

    logger.info(f"[OCR] Starting processing of screenshot job {job_id}")

    if job is None:
        logger.debug(f"[OCR] Fetching job data for job {job_id}")
        job = get_match_screenshot(job_id)
    if job is None:
        logger.error(f"[OCR] Job {job_id} not found in database")
        raise ValueError(f"Job de screenshot {job_id} não encontrado")

    image_data_size = len(job["image_data"]) if job.get("image_data") else 0
    logger.info(f"[OCR] Processing job {job_id}: image_size={image_data_size} bytes, status={job.get('status')}")

    # Try LLM-based parsing first
    logger.debug(f"[OCR] Attempting LLM-based parsing for job {job_id}")
    parsed = _parse_image_with_llm(job["image_data"])
    
    if parsed is None:
        logger.info(f"[OCR] LLM parsing failed for job {job_id}, falling back to OCR text extraction")
        try:
            raw_text = extract_text_from_image_data(job["image_data"])
            logger.debug(f"[OCR] Extracted text from image: {len(raw_text)} characters")
            parsed = parse_dota_match_text(raw_text, "")
            logger.info(f"[OCR] Fallback parsing completed for job {job_id}")
        except Exception as e:
            logger.error(f"[OCR] Fallback OCR parsing failed for job {job_id}: {e}")
            parsed = {"valid_dota_screenshot": False, "error": str(e)}
    else:
        logger.info(f"[OCR] LLM parsing successful for job {job_id}")

    metadata = json.dumps(parsed, ensure_ascii=False)
    logger.debug(f"[OCR] Generated metadata for job {job_id}: {len(metadata)} characters")

    if parsed.get("valid_dota_screenshot") is False:
        logger.warning(f"[OCR] Job {job_id} marked as invalid Dota screenshot")
        set_match_screenshot_status(job_id, "failed", metadata=metadata)
        logger.info(f"[OCR] Job {job_id} processing completed (failed)")
        return {"job": job, "parsed": parsed}

    logger.info(f"[OCR] Job {job_id} processing completed successfully")
    set_match_screenshot_status(job_id, "processed", metadata=metadata)
    return {"job": job, "parsed": parsed}

