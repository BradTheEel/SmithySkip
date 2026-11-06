static float smithy_delta(void *object, float dt, uintptr_t caller, int shift) {
    union { float f; uint32_t u; } delta = { .f = dt };
    if (shift || !game_base || caller != game_base + ANIMATION_CALLER_RVA ||
        delta.u == 0 || (delta.u & 0x80000000u) ||
        (delta.u & 0x7F800000u) == 0x7F800000u || dt > 2.0f || bypass_requested()) return dt;
#ifndef SMITHY_SKIP_TEST
    if (!animation_signatures_match((const unsigned char *)game_base, ANIMATION_WRAPPER_PREFIX_LENGTH)) return dt;
#endif
    const unsigned char *ui = object;
    uint64_t owner, resource, vtable;
    if (!readable_range(ui, SUBTITLE_READ_SIZE) ||
        !read_u64(ui, SUBTITLE_OWNER_OFFSET, &owner) || !owner ||
        !readable_range((const void *)(uintptr_t)owner, NPC_RESOURCE_READ_SIZE) ||
        !read_u64((const void *)(uintptr_t)owner, 0, &vtable) || vtable != game_base + NPC_VTABLE_RVA ||
        !read_u64((const void *)(uintptr_t)owner, NPC_RESOURCE_OFFSET, &resource)) return dt;

    const Profile *profile = profile_for_resource((const void *)(uintptr_t)resource);
    uint32_t value32, active_hash;
    uint64_t manager, select, value;
    PendingHudSnapshot pending_initial = {0}, pending_final = {0};
    if (!profile || !readable_range(ui, SUBTITLE_READ_SIZE) ||
        !read_u64(ui, 0, &vtable) || vtable != game_base + SUBTITLE_VT_RVA || ui[0xC] != 8 ||
        !read_u32(ui, UI_STATE_OFFSET, &value32) || value32 != 1 ||
        !read_u32(ui, SUBTITLE_ACTIVE_HASH_OFFSET, &active_hash) || !key_in_profile(profile, active_hash) ||
        !read_u64(ui, SUBTITLE_OWNER_OFFSET, &owner) || !owner ||
        !read_u64((const void *)game_base, MANAGER_GLOBAL_RVA, &manager) || !manager) return dt;

    const void *manager_object = (const void *)(uintptr_t)manager;
    const void *npc = (const void *)(uintptr_t)owner;
    if (!read_u64(manager_object, 0, &vtable) || vtable != game_base + MANAGER_VT_RVA ||
        !read_u64(manager_object, MANAGER_SUBTITLE_OFFSET, &value) || value != (uintptr_t)ui ||
        !safe_pending_hud(manager, &pending_initial) ||
        !read_u64(manager_object, MANAGER_SELECT_OFFSET, &select) || !idle_select(select) ||
        !readable_range(npc, NPC_RESOURCE_READ_SIZE) ||
        !read_u64(npc, 0, &vtable) || vtable != game_base + NPC_VTABLE_RVA ||
        !read_u64(npc, NPC_RESOURCE_OFFSET, &resource) ||
        !valid_resource((const void *)(uintptr_t)resource, profile) ||
        !read_u32(npc, NPC_TALK_BUSY_OFFSET, &value32) || value32 ||
        !read_u64(npc, NPC_TALK_CONTEXT_OFFSET, &value) ||
        (value && (!readable_range((const void *)(uintptr_t)value, 0x22) ||
            ((const unsigned char *)(uintptr_t)value)[0x21]))) return dt;

    SubtitleQueue queue;
    SubtitleQueueScan scan;
    if (!read_subtitle_queue(ui, &queue) ||
        !validate_subtitle_rows(ui, &queue, profile, owner, SUBTITLE_IGNORE_DEADLINES, &scan)) return dt;

    if (!read_u32(ui, UI_STATE_OFFSET, &value32) || value32 != 1 ||
        !read_u32(ui, SUBTITLE_ACTIVE_HASH_OFFSET, &value32) || value32 != active_hash ||
        !read_u64(ui, SUBTITLE_OWNER_OFFSET, &value) || value != owner ||
        !read_u64(manager_object, MANAGER_SUBTITLE_OFFSET, &value) || value != (uintptr_t)ui ||
        !read_u64(manager_object, MANAGER_SELECT_OFFSET, &value) || value != select || !idle_select(select) ||
        !safe_pending_hud(manager, &pending_final) ||
        !equal_bytes((const unsigned char *)&pending_initial, (const unsigned char *)&pending_final, sizeof(pending_final)) ||
        !read_u64(ui, SUBTITLE_QUEUE_HEAD_OFFSET, &value) || value != queue.head ||
        !read_u64(ui, SUBTITLE_QUEUE_COUNT_OFFSET, &value) || value != queue.count ||
        !read_u64(ui, SUBTITLE_TEXT_HEAD_OFFSET, &value) || value != queue.head ||
        !read_u64(ui, SUBTITLE_TEXT_COUNT_OFFSET, &value) || value != queue.count) return dt;
    return dt * 4.0f;
}
