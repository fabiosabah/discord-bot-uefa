# -*- coding: utf-8 -*-
# Facade de compatibilidade — re-exporta tudo dos novos módulos core/db/*.
# Todos os imports existentes continuam funcionando sem alteração.
# Migre os callers para importar diretamente de core.db.* e remova este arquivo.

from core.db.connection import (
    get_connection,
    init_db,
    migrate_db,
)

from core.db.player_repo import (
    upsert_player,
    add_win,
    remove_win,
    add_loss,
    remove_loss,
    get_player,
    find_player_by_display_name,
    resolve_player_names_exact,
    resolve_player_names,
    delete_player,
    get_ranking,
    get_captains_from_list,
    add_player_alias,
    remove_player_alias,
    get_player_aliases,
)

from core.db.audit_repo import (
    log_action,
    log_match_action,
    get_last_admin_action,
    delete_audit_log_entry,
    count_match_deletions_today,
    get_raw_match_audit_events,
    get_last_update,
)

from core.db.ocr_repo import (
    enqueue_match_screenshot,
    is_match_screenshot_enqueued,
    get_pending_match_screenshots,
    set_match_screenshot_status,
    get_match_screenshot,
    delete_match_screenshot,
    delete_match_screenshots,
)

from core.db.lobby_repo import (
    get_list_channel,
    get_image_channel,
    set_list_channel,
    set_image_channel,
    clear_image_channel,
    clear_list_channel,
    save_lobby_session,
    delete_lobby_session,
    get_lobby_sessions,
)

from core.db.match_repo import (
    get_all_hero_stats_from_matches,
    get_hero_match_history,
    get_league_hero_winrates_from_matches,
    insert_ocr_match,
    insert_match_import,
    insert_match_history_from_ocr_import,
    insert_league_match,
    get_match_by_league_id,
    get_ranking_from_matches,
    get_last_ocr_match_info,
    get_match_created_at,
    get_next_match_id,
    get_pending_match_id_for_opposite_result,
    get_pending_match_id_for_same_side,
    record_match_history,
    update_match_hero,
    update_league_match_heroes,
    update_league_match_hero_by_slot,
    update_league_match_player_names,
    update_league_match_player_name_by_slot,
    update_league_match_duration,
    delete_match_history,
    delete_league_match,
    delete_match_history_by_audit_id,
    create_or_replace_manual_match,
    get_player_history_stats,
    get_player_match_history,
    get_player_streak,
    get_player_top_opponents,
    get_player_top_teammates,
    get_player_top_heroes,
    get_match_summary,
    get_recent_match_ids,
    get_recent_match_summaries,
    get_player_match_stats_from_matches,
    get_player_top_heroes_from_matches,
    get_player_top_teammates_from_matches,
    get_player_top_opponents_from_matches,
    get_player_top_heroes_with_winrate_from_matches,
    get_player_head_to_head_from_matches,
    get_player_teammate_balance_from_matches,
    get_player_match_history_from_matches,
    get_player_streak_from_matches,
    get_streak_highlights_from_matches,
    find_unregistered_match_players,
    diagnose_and_fix_kda_data,
)
