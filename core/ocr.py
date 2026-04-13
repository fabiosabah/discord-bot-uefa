# -*- coding: utf-8 -*-
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("OCR")


def can_process_ocr() -> bool:
    try:
        from google.cloud import vision  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("GOOGLE_CLOUD_VISION_API_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))


def can_process_llm() -> bool:
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("OPENAI_API_KEY"))


def _build_vision_client():
    try:
        from google.cloud import vision
        from google.api_core.client_options import ClientOptions
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-vision is required. Install it with `pip install google-cloud-vision`."
        ) from exc

    api_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
    if api_key:
        return vision.ImageAnnotatorClient(client_options=ClientOptions(api_key=api_key))

    return vision.ImageAnnotatorClient()


def _build_openai_client():
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError(
            "openai is required. Install it with `pip install openai`."
        ) from exc

    openai.api_key = os.getenv("OPENAI_API_KEY")
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY is required for LLM processing.")

    api_base = os.getenv("OPENAI_API_BASE")
    if api_base:
        openai.api_base = api_base

    return openai


def extract_text_from_image_url(image_url: str) -> str:
    from google.cloud import vision

    client = _build_vision_client()
    image = vision.Image(source=vision.ImageSource(image_uri=image_url))
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    return response.full_text_annotation.text or ""


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
    pattern = re.compile(r"^(?P<name>.+?)\s+(?P<score>\d+\s*[/x]\s*\d+\s*[/x]\s*\d+|\d+)\s+(?P<gold>\d{1,3}(?:[.,]\d{3})*)(?:\s+gold)?", re.IGNORECASE)
    for line in lines:
        match = pattern.search(line)
        if match:
            name = match.group("name")
            score = match.group("score")
            gold_text = match.group("gold")
            try:
                gold = int(gold_text.replace(".", "").replace(",", ""))
            except ValueError:
                gold = None
            players.append({"name": name, "score": score, "gold": gold, "raw_line": line})
    return players


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

    game_details = payload.get("game_details")
    teams = payload.get("teams")
    if not game_details or not teams:
        return None

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


def _build_llm_prompt(raw_text: str, image_url: str | None = None) -> list[dict[str, str]]:
    messages = [
        {
            "role": "system",
            "content": (
                "Você é um assistente especialista em Dota 2. Recebe texto extraído de uma imagem de placar "
                "ou de detalhes de partida e deve retornar apenas JSON válido com as informações da partida. "
                "Não responda em markdown ou texto adicional."
            )
        },
        {
            "role": "user",
            "content": (
                "Extraia o máximo de informações possíveis sobre a partida. Retorne um objeto JSON com os campos: "
                "steam_match_id, dota_match_id, match_date, mode, winner, duration, score, teams. "
                "O campo score deve ser um objeto com radiant e dire. "
                "O campo teams deve conter radiant e dire, cada um com uma lista de jogadores contendo: player, hero, kda, net_worth. "
                "Se não houver algum campo, coloque null ou não inclua. "
                "Use o texto a seguir para extrair esses valores."
            )
        }
    ]

    if image_url:
        messages.append({
            "role": "user",
            "content": f"Imagem de origem: {image_url}"
        })

    messages.append({
        "role": "user",
        "content": f"Texto OCR:\n{raw_text}"
    })
    return messages


def _parse_text_with_llm(raw_text: str, image_url: str | None = None) -> dict[str, Any] | None:
    if not can_process_llm():
        return None

    openai = _build_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = _build_llm_prompt(raw_text, image_url)

    completion = openai.ChatCompletion.create(
        model=model,
        messages=prompt,
        temperature=0.0,
        max_tokens=800
    )

    content = completion.choices[0].message.content
    parsed = _parse_json_payload(content)
    if parsed is not None:
        return parsed

    # If the LLM output is not pure JSON, try to extract JSON from it.
    return _parse_json_payload(str(content))


def parse_dota_match_text(raw_text: str, image_url: str | None = None) -> dict[str, Any]:
    parsed = _parse_json_payload(raw_text)
    if parsed is not None:
        return parsed

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
    set_match_screenshot_status(job_id, "processed", metadata=metadata)
    return {"job": job, "parsed": parsed}
