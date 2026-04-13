# -*- coding: utf-8 -*-
import json
import logging
import os
import re
from typing import Any

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
        except Exception:
            pass

    return ""


def extract_text_from_image_url(image_url: str) -> str:
    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-1.5-flash"
    instructions = (
        "Você é um assistente especializado em Dota 2. Leia a imagem e retorne apenas o texto visível contido nela. "
        "Não adicione explicações, marcações ou comentários. Retorne o texto bruto."
    )

    if provider == "gemini":
        from google.genai import types

        text_prompt = f"{instructions}\nImagem: {image_url}"
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=text_prompt)]
            )
        ]

        response_text = ""
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")
            ),
        ):
            chunk_text = getattr(chunk, "text", None)
            if chunk_text:
                response_text += chunk_text

        text = response_text
    else:
        response = client.responses.create(
            model=model,
            input={
                "type": "input_image",
                "image_url": image_url,
                "detail": "high",
            },
            instructions=instructions,
            temperature=0.0,
            max_output_tokens=1000,
        )
        text = _extract_text_from_response(response)

    if not text:
        raise RuntimeError("Falha ao extrair texto da imagem via Gemini.")
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
            name = match.group("name")
            score = match.group("score")
            gold_text = match.group("gold")
            hero = match.group("hero")
            try:
                gold = int(gold_text.replace(".", "").replace(",", ""))
            except ValueError:
                gold = None
            players.append({
                "name": name,
                "score": score,
                "gold": gold,
                "hero": hero.strip() if isinstance(hero, str) and hero.strip() else None,
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

    if payload.get("valid_dota_screenshot") is False:
        return {
            "raw_text": raw_text,
            "valid_dota_screenshot": False,
            "metadata_payload": payload,
        }

    match_info = payload.get("match_info")
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
            team_list = teams.get(team_name)
            if not isinstance(team_list, list):
                continue
            for entry in team_list:
                if not isinstance(entry, dict):
                    continue
                parsed_players.append({
                    "name": entry.get("player"),
                    "hero": entry.get("hero"),
                    "score": entry.get("kda"),
                    "net_worth": entry.get("net_worth"),
                    "team": team_name,
                    "raw_entry": entry,
                })

        return {
            "raw_text": raw_text,
            "steam_match_id": payload.get("steam_match_id"),
            "dota_match_id": payload.get("dota_match_id"),
            "match_date": payload.get("match_date") or match_info.get("date"),
            "mode": match_info.get("game_mode"),
            "winner": winner,
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

    game_details = payload.get("game_details")
    teams = payload.get("teams")
    if game_details and teams:
        radiant_score = None
        dire_score = None
        winner = game_details.get("winner")
        score = game_details.get("score") or {}
        if isinstance(score, dict):
            radiant_score = score.get("radiant")
            dire_score = score.get("dire")

        parsed_players: list[dict[str, Any]] = []
        for team_name in ("radiant", "dire"):
            team_list = teams.get(team_name) or []
            for entry in team_list:
                if not isinstance(entry, dict):
                    continue
                parsed_players.append({
                    "name": entry.get("player"),
                    "hero": entry.get("hero"),
                    "score": entry.get("kda"),
                    "net_worth": entry.get("net_worth"),
                    "team": team_name,
                    "raw_entry": entry,
                })

        return {
            "raw_text": raw_text,
            "steam_match_id": payload.get("steam_match_id"),
            "dota_match_id": payload.get("dota_match_id"),
            "match_date": payload.get("match_date") or game_details.get("date"),
            "mode": game_details.get("mode"),
            "winner": winner,
            "duration": game_details.get("duration"),
            "radiant_win": isinstance(winner, str) and winner.lower() == "radiant",
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
            parsed_players.append({
                "name": entry.get("name"),
                "hero": entry.get("hero"),
                "score": entry.get("score"),
                "net_worth": entry.get("net_worth"),
                "team": entry.get("team"),
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


def _build_llm_prompt(raw_text: str, image_url: str | None = None) -> str:
    prompt = (
        "Você é um assistente especialista em Dota 2. Recebe texto extraído de uma imagem de placar "
        "ou de detalhes de partida e deve retornar apenas JSON válido com as informações da partida. "
        "Não responda em markdown ou texto adicional. Retorne um objeto JSON com a estrutura abaixo. "
        "Use null quando algum valor não puder ser extraído. Se o texto não for um placar de Dota 2, "
        "retorne apenas {\"valid_dota_screenshot\": false}.\n\n"
        "Exemplo de formato desejado:\n"
        "{\n"
        "  \"valid_dota_screenshot\": true,\n"
        "  \"match_info\": {\n"
        "    \"game_mode\": \"Captains Mode\",\n"
        "    \"duration\": \"51:29\",\n"
        "    \"winner\": \"Dire\",\n"
        "    \"score\": {\"radiant\": 37, \"dire\": 40}\n"
        "  },\n"
        "  \"teams\": {\n"
        "    \"radiant\": [\n"
        "      {\"player\": \"WFz [-pRs-]\", \"hero\": \"Beastmaster\", \"kda\": \"10 / 13 / 18\", \"net_worth\": 25632},\n"
        "      ...\n"
        "    ],\n"
        "    \"dire\": [ ... ]\n"
        "  }\n"
        "}"
    )

    if image_url:
        prompt += f"\nImagem de origem: {image_url}."

    prompt += f"\nTexto OCR:\n{raw_text}"
    return prompt


def _parse_text_with_llm(raw_text: str, image_url: str | None = None) -> dict[str, Any] | None:
    if not can_process_llm():
        return None

    provider, client = _build_ai_client()
    model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-1.5-flash"
    prompt = _build_llm_prompt(raw_text, image_url)

    if provider == "gemini":
        from google.genai import types

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )
        ]

        response_text = ""
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")
            ),
        ):
            chunk_text = getattr(chunk, "text", None)
            if chunk_text:
                response_text += chunk_text

        content = response_text
    else:
        response = client.responses.create(
            model=model,
            input=raw_text,
            instructions=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "dota_match_data",
                    "description": "Estrutura JSON com match_info e teams para partida Dota 2",
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
                                                "kda": {"type": ["string", "null"]},
                                                "net_worth": {"type": ["integer", "null"]}
                                            },
                                            "required": ["player", "hero", "kda", "net_worth"],
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
                                                "kda": {"type": ["string", "null"]},
                                                "net_worth": {"type": ["integer", "null"]}
                                            },
                                            "required": ["player", "hero", "kda", "net_worth"],
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
    parsed = _parse_json_payload(raw_text)
    if parsed is not None:
        if parsed.get("valid_dota_screenshot") is False:
            return parsed
        return parsed

    if not _is_probably_dota_score_text(raw_text):
        return {
            "raw_text": raw_text,
            "valid_dota_screenshot": False,
            "reason": "text_does_not_match_dota_score",
        }

    parsed = _parse_text_with_llm(raw_text, image_url)
    if parsed is not None:
        return parsed

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
    from core.database import get_match_screenshot, set_match_screenshot_status

    if job is None:
        job = get_match_screenshot(job_id)
    if job is None:
        raise ValueError(f"Job de screenshot {job_id} não encontrado")

    raw_text = extract_text_from_image_url(job["image_url"])
    parsed = parse_dota_match_text(raw_text, job["image_url"])
    metadata = json.dumps(parsed, ensure_ascii=False)

    if parsed.get("valid_dota_screenshot") is False:
        set_match_screenshot_status(job_id, "failed", metadata=metadata)
        return {"job": job, "parsed": parsed}

    set_match_screenshot_status(job_id, "processed", metadata=metadata)
    return {"job": job, "parsed": parsed}
