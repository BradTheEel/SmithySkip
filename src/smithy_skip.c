/* SmithySkip 0.6.7 - MIT.
 * Uses the native subtitle queue's bulk completion only after every pending
 * line, localized text, and NPC callback has passed the 0.4 whitelist.
 * All releases, callbacks, queue advancement, and close state remain native.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef SMITHY_SKIP_TEST
#include "vendor/include/MinHook.h"
#endif
#include <windows.h>
#include <stdint.h>
#include <intrin.h>
#include "game_layout.h"
#include "input_bypass.h"

typedef struct Key { uint32_t hash_eng, hash_rus; const char *name; } Key;
typedef struct Profile {
    const char *path;
    const char *name;
    uint32_t count;
    uint64_t fingerprint;
    const Key *keys;
    unsigned key_count;
} Profile;
#include "profiles.h"

typedef void (*UpdateFn)(void *);
typedef void (*CloseFn)(void *, unsigned char);
typedef uint32_t (*CrcFn)(const char *, uint32_t);
static UpdateFn original_update;
static CloseFn request_close;
static uintptr_t game_base;
static void *hidden_gui;
static unsigned hidden_original_draw;
static CrcFn crc_override;

static int readable_range(const void *pointer, size_t length) {
    uintptr_t begin=(uintptr_t)pointer;
    if (!pointer || !length || begin>UINTPTR_MAX-length) return 0;
    uintptr_t end=begin+length;
    while (begin<end) {
        MEMORY_BASIC_INFORMATION info;
        if (!VirtualQuery((const void *)begin,&info,sizeof(info)) ||
            info.State!=MEM_COMMIT || (info.Protect&PAGE_GUARD) ||
            (info.Protect&PAGE_NOACCESS)) return 0;
        DWORD protect=info.Protect&0xffu;
        if (protect!=PAGE_READONLY && protect!=PAGE_READWRITE &&
            protect!=PAGE_WRITECOPY && protect!=PAGE_EXECUTE_READ &&
            protect!=PAGE_EXECUTE_READWRITE && protect!=PAGE_EXECUTE_WRITECOPY)
            return 0;
        uintptr_t region=(uintptr_t)info.BaseAddress;
        if (region>UINTPTR_MAX-info.RegionSize || region+info.RegionSize<=begin)
            return 0;
        begin=region+info.RegionSize<end ? region+info.RegionSize : end;
    }
    return 1;
}

static int read_u64(const void *base,size_t offset,uint64_t *value) {
    uintptr_t address=(uintptr_t)base;
    if (!base || address>UINTPTR_MAX-offset ||
        !readable_range((const void *)(address+offset),sizeof(*value))) return 0;
    *value=*(const uint64_t *)(address+offset);
    return 1;
}

static int writable_byte(void *pointer) {
    MEMORY_BASIC_INFORMATION info;
    if (!pointer || !VirtualQuery(pointer,&info,sizeof(info)) ||
        info.State!=MEM_COMMIT || (info.Protect&(PAGE_GUARD|PAGE_NOACCESS))) return 0;
    DWORD protect=info.Protect&0xffu;
    return protect==PAGE_READWRITE || protect==PAGE_WRITECOPY ||
        protect==PAGE_EXECUTE_READWRITE || protect==PAGE_EXECUTE_WRITECOPY;
}

static int read_u32(const void *base,size_t offset,uint32_t *value) {
    uintptr_t address=(uintptr_t)base;
    if (!base || address>UINTPTR_MAX-offset ||
        !readable_range((const void *)(address+offset),sizeof(*value))) return 0;
    *value=*(const uint32_t *)(address+offset);
    return 1;
}

static int equal_bytes(const unsigned char *a, const unsigned char *b, size_t n) {
    for (size_t i=0;i<n;++i) if (a[i]!=b[i]) return 0;
    return 1;
}

static const unsigned char animation_call_signature[] = {
    0x48,0x8b,0xcb,0xe8,0xe0,0xc6,0x39,0x00
};
static const unsigned char animation_wrapper_signature[] = {
    0xf3,0x0f,0x10,0x89,0xc8,0x02,0x00,0x00,0xf3,0x0f,0x59,0x49,0x6c,
    0x48,0x8b,0x05,0x54,0x01,0xe7,0x02,0xf3,0x0f,0x5e,0x48,0x58,
    0xe9,0x72,0x03,0x00,0x00
};

_Static_assert(sizeof(animation_call_signature) == ANIMATION_CALL_LENGTH,
    "The return address must immediately follow the call signature");
_Static_assert(sizeof(animation_wrapper_signature) == ANIMATION_WRAPPER_LENGTH,
    "The install check must cover the complete wrapper signature");
_Static_assert(ANIMATION_WRAPPER_PREFIX_LENGTH + 5u == ANIMATION_WRAPPER_LENGTH,
    "The runtime check must stop at the patched five-byte jump");

static int animation_signatures_match(const unsigned char *base, size_t wrapper_length) {
    if (!base || wrapper_length > sizeof(animation_wrapper_signature)) return 0;
    const unsigned char *call = base + ANIMATION_CALL_RVA;
    const unsigned char *wrapper = base + ANIMATION_WRAPPER_RVA;
    return readable_range(call, sizeof(animation_call_signature)) &&
        readable_range(wrapper, wrapper_length) &&
        equal_bytes(call, animation_call_signature, sizeof(animation_call_signature)) &&
        equal_bytes(wrapper, animation_wrapper_signature, wrapper_length);
}

static int matches_path(const unsigned char *resource,const char *target) {
    const unsigned char *name=resource+0x0C;
    unsigned i=0;
    for (;target[i];++i) {
        if (!readable_range(name+i,1)) return 0;
        unsigned char ch=name[i];
        if (ch=='\\') ch='/';
        if (ch>='A' && ch<='Z') ch+='a'-'A';
        if (ch!=(unsigned char)target[i]) return 0;
    }
    return readable_range(name+i,1) && name[i]==0;
}

/* Validate only serialized fields; never write a resource or its entries. */
static int valid_resource(const unsigned char *resource,const Profile *p) {
    if (!resource || !readable_range(resource,0xD0) ||
        !matches_path(resource,p->path) ||
        *(const uint16_t *)(resource+0xA8)!=500 ||
        *(const uint32_t *)(resource+0xB8)!=p->count) return 0;
    unsigned char *const *rows=*(unsigned char *const *const *)(resource+0xC8);
    if (!rows || !readable_range(rows,(size_t)p->count*sizeof(*rows))) return 0;
    static const unsigned ranges[][2]={{8,9},{18,4},{24,18},{44,20}};
    uint64_t h=UINT64_C(0xCBF29CE484222325);
    for (unsigned i=0;i<p->count;++i) {
        if (!rows[i] || !readable_range(rows[i],64)) return 0;
        for (unsigned r=0;r<4;++r)
            for (unsigned j=0;j<ranges[r][1];++j)
                h=(h^rows[i][ranges[r][0]+j])*UINT64_C(0x100000001B3);
    }
    return h==p->fingerprint;
}

static const Key *current_key(const void *object) {
    const unsigned char *ui=(const unsigned char *)object;
    if (!ui) return 0;
    uint32_t hash=*(const uint32_t *)(ui+SUBTITLE_ACTIVE_HASH_OFFSET);
    if (!hash) return 0;
    /* +28F4 hashes the raw localized UTF-8 message, not its GMD label.
     * +3E98 is the current completion listener; in NPC talk it is uNpcVillage.
     * +3EC0 is an optional speaker-name string, never an NPC pointer.
     * Both offsets were checked in the native message-activation routine and
     * against a read-only snapshot of a live Research Base conversation. */
    for (unsigned i=0;i<sizeof(profiles)/sizeof(profiles[0]);++i) {
        const Profile *p=&profiles[i];
        for (unsigned j=0;j<p->key_count;++j) {
            const Key *key=&p->keys[j];
            if (key->hash_eng!=hash && key->hash_rus!=hash) continue;
            const unsigned char *npc=*(const unsigned char *const *)(ui+SUBTITLE_OWNER_OFFSET);
            if (!npc || *(const uintptr_t *)npc!=game_base+NPC_VTABLE_RVA) return 0;
            const unsigned char *resource=*(const unsigned char *const *)(npc+NPC_RESOURCE_OFFSET);
            if (valid_resource(resource,p)) return key;
        }
    }
    return 0;
}

static const Profile *profile_for_key(const Key *key) {
    if (!key) return 0;
    for (unsigned i=0;i<sizeof(profiles)/sizeof(profiles[0]);++i) {
        const Profile *p=&profiles[i];
        for (unsigned j=0;j<p->key_count;++j)
            if (&p->keys[j]==key) return p;
    }
    return 0;
}

static const Key *key_in_profile(const Profile *profile,uint32_t hash) {
    if (!profile || !hash) return 0;
    for (unsigned i=0;i<profile->key_count;++i) {
        const Key *key=&profile->keys[i];
        if (key->hash_eng==hash || key->hash_rus==hash) return key;
    }
    return 0;
}

static const Profile *profile_for_resource(const unsigned char *resource) {
 for(unsigned i=0;i<sizeof(profiles)/sizeof(profiles[0]);i++)
  if(valid_resource(resource,&profiles[i]))return &profiles[i];
 return 0;
}

typedef struct IdleSelectSnapshot { uint64_t vt,listener,head,count,callback; uint32_t state,flags; } IdleSelectSnapshot;
static int read_idle_select(uint64_t select,IdleSelectSnapshot *out) {
 void *o=(void*)(uintptr_t)select;
 if(!select||!readable_range(o,0x2c40)||!read_u64(o,0,&out->vt)||out->vt!=game_base+SELECT_VT_RVA||
  !read_u32(o,UI_STATE_OFFSET,&out->state)||out->state||!read_u32(o,0x2298,&out->flags)||(out->flags&0x80000011u)||
  !read_u64(o,0x2c38,&out->count)||out->count||!read_u64(o,0x2c30,&out->head)||out->head>=4||
  !read_u64(o,0x29e0,&out->callback)||out->callback||!read_u64(o,0x2100,&out->listener)||out->listener)return 0;
 return 1;
}
static int idle_select(uint64_t select) {
 IdleSelectSnapshot a={0},b={0};
 return read_idle_select(select,&a)&&read_idle_select(select,&b)&&equal_bytes((const unsigned char*)&a,(const unsigned char*)&b,sizeof(a));
}

typedef struct PendingHudEntry { uint64_t callable,vt,invoke,owner,argument; } PendingHudEntry;
typedef struct PendingHudSnapshot { uint64_t count,open,hud,hud_vt; uint32_t hud_life,reserved; PendingHudEntry entry[3]; } PendingHudSnapshot;
static int read_pending_hud(uint64_t manager,PendingHudSnapshot *out) {
 void *m=(void*)(uintptr_t)manager;
 if(!manager||!read_u64(m,0x8f60,&out->count)||out->count>3||
  !read_u64(m,0xbd68,&out->open)||out->open)return 0;
 if(!out->count)return 1;
 if(manager>UINTPTR_MAX-0x50d8||!read_u64(m,0x14180,&out->hud)||!out->hud||
  !read_u64((void*)(uintptr_t)out->hud,0,&out->hud_vt)||out->hud_vt!=game_base+0x3471398u||
  !read_u32((void*)(uintptr_t)out->hud,0xc,&out->hud_life)||!(out->hud_life&0xeu))return 0;
 for(uint64_t i=0;i<out->count;i++) {
  PendingHudEntry *e=&out->entry[i];
  size_t offset=(size_t)i*0x80u;
  if(!read_u64(m,0x4f98u+offset,&e->callable)||e->callable!=manager+0x4f60u+offset||
   !read_u64((void*)(uintptr_t)e->callable,0,&e->vt)||e->vt!=game_base+0x33f9c90u||
   !read_u64((void*)(uintptr_t)e->vt,0x10,&e->invoke)||e->invoke!=game_base+0x1ad4f10u||
   !read_u64((void*)(uintptr_t)e->callable,8,&e->owner)||e->owner!=manager||
   !read_u64((void*)(uintptr_t)e->callable,0x10,&e->argument))return 0;
 }
 return 1;
}
static int safe_pending_hud(uint64_t manager,PendingHudSnapshot *out) {
 PendingHudSnapshot a={0},b={0};
 if(!read_pending_hud(manager,&a)||!read_pending_hud(manager,&b)||!equal_bytes((const unsigned char*)&a,(const unsigned char*)&b,sizeof(a)))return 0;
 *out=a;return 1;
}

typedef struct SubtitleQueue {
    uint64_t head;
    uint64_t count;
} SubtitleQueue;

typedef enum SubtitleDeadlinePolicy {
    SUBTITLE_CHECK_FIRST_OPEN_DEADLINES,
    SUBTITLE_IGNORE_DEADLINES
} SubtitleDeadlinePolicy;

typedef struct SubtitleQueueScan {
    uint64_t messages[SUBTITLE_QUEUE_CAPACITY];
    unsigned prefix_count;
} SubtitleQueueScan;

/* Each caller has already checked the readable subtitle UI range. */
static int read_subtitle_queue(const void *object, SubtitleQueue *out) {
    const unsigned char *ui = object;
    uint64_t text_head, text_count;
    return read_u64(ui, SUBTITLE_QUEUE_HEAD_OFFSET, &out->head) &&
        read_u64(ui, SUBTITLE_QUEUE_COUNT_OFFSET, &out->count) &&
        read_u64(ui, SUBTITLE_TEXT_HEAD_OFFSET, &text_head) &&
        read_u64(ui, SUBTITLE_TEXT_COUNT_OFFSET, &text_count) &&
        out->head < SUBTITLE_QUEUE_CAPACITY && out->head == text_head &&
        out->count <= SUBTITLE_QUEUE_CAPACITY && out->count == text_count &&
        ui[SUBTITLE_BULK_OFFSET] == 0;
}

/* Check every row, including the unflagged tail of the queue. */
static int validate_subtitle_rows(const void *object, const SubtitleQueue *queue,
    const Profile *profile, uint64_t owner, SubtitleDeadlinePolicy deadlines,
    SubtitleQueueScan *out) {
    const unsigned char *ui = object;
    CrcFn crc = crc_override ? crc_override : (CrcFn)(game_base + NATIVE_CRC_RVA);
    out->prefix_count = 0;
    for (uint64_t i = 0; i < queue->count; ++i) {
        size_t slot = (queue->head + i) % SUBTITLE_QUEUE_CAPACITY;
        const unsigned char *text_row = ui + SUBTITLE_TEXT_ROWS_OFFSET + slot * SUBTITLE_TEXT_ROW_SIZE;
        const unsigned char *metadata = ui + SUBTITLE_METADATA_ROWS_OFFSET + slot * SUBTITLE_METADATA_ROW_SIZE;
        uint64_t message, row_owner;
        if (!read_u64(text_row, 0, &message) ||
            !read_u64(text_row, SUBTITLE_ROW_OWNER_OFFSET, &row_owner) ||
            row_owner != owner) return 0;

        if (deadlines == SUBTITLE_CHECK_FIRST_OPEN_DEADLINES) {
            uint32_t deadline;
            if (!read_u32(text_row, SUBTITLE_ROW_DEADLINE_OFFSET, &deadline) ||
                deadline != (i == 0 ? 0x3F000000u : 0u)) return 0;
        }

        uint32_t kind, flags;
        if (!read_u32(metadata, 0, &kind) || kind != 0 ||
            !read_u32(metadata, SUBTITLE_METADATA_FLAGS_OFFSET, &flags) || (flags & ~0x11u)) return 0;
        if (flags & 0x10u) {
            if (out->prefix_count != i) return 0;
            ++out->prefix_count;
        }

        uint32_t length;
        const unsigned char *string = (const unsigned char *)(uintptr_t)message;
        if (!read_u32(string, 4, &length) || length == 0 || length > 4096 ||
            !readable_range(string, 8u + (size_t)length + 1u)) return 0;
        const char *text = (const char *)string + 8;
        for (uint32_t n = 0; n < length; ++n) if (text[n] == 0) return 0;
        if (text[length] != 0 || !key_in_profile(profile, crc(text, UINT32_C(0xFFFFFFFF)))) return 0;
        out->messages[i] = message;
    }
    return 1;
}

#include "animation_policy.h"

/* Queue layouts were verified in the matching executable. The two FIFO rings
 * are parallel: metadata slots are 24 bytes; MtString slots are 80 bytes.
 * Reject any mismatch before asking the game's native finish path to consume
 * them. Every pending line must be a 0.4 ordinary key, including unflagged
 * rows, and every queued callback must be the active NPC listener. */
static int bulk_preflight(const void *ui, const Key *active_key) {
    const Profile *profile = profile_for_key(active_key);
    uint64_t owner, resource;
    SubtitleQueue queue;
    SubtitleQueueScan scan;
    if (!profile || !readable_range(ui, SUBTITLE_READ_SIZE) ||
        !read_u64(ui, SUBTITLE_OWNER_OFFSET, &owner) || !owner ||
        !readable_range((const void *)(uintptr_t)owner, sizeof(uintptr_t)) ||
        *(const uintptr_t *)(uintptr_t)owner != game_base + NPC_VTABLE_RVA ||
        !read_u64((const void *)(uintptr_t)owner, NPC_RESOURCE_OFFSET, &resource) ||
        !valid_resource((const unsigned char *)(uintptr_t)resource, profile) ||
        !read_subtitle_queue(ui, &queue) || queue.count == 0) return 0;
    return validate_subtitle_rows(ui, &queue, profile, owner, SUBTITLE_IGNORE_DEADLINES, &scan) &&
        scan.prefix_count != 0;
}

/* Temporarily suppress only the subtitle unit's drawing. Move/update stays
 * enabled. Release our mask before a protected or unrelated line is drawn.
 * Never restore a pointer to a previous (possibly unloaded) UI instance. */
static void draw_mask(void *object,int hide) {
    unsigned char *ui=(unsigned char *)object;
    if (hidden_gui && hidden_gui!=object) {
        hidden_gui=0;
        hidden_original_draw=0;
    }
    uint32_t *flags=(uint32_t *)(ui+0x14);
    if (hide) {
        if (!hidden_gui) {
            hidden_gui=object;
            hidden_original_draw=*flags&2u;
        }
        *flags&=~2u;
    } else if (hidden_gui==object) {
        if (hidden_original_draw) *flags|=2u;
        hidden_gui=0;
        hidden_original_draw=0;
    }
}


static void process_tick(void *ui,int bypass) {
    bypass = bypass || bypass_requested();
    /* Let the native update activate/advance the current line first. A close
     * request based on the previous line must not affect a new protected one. */
    original_update(ui);
    const Key *key=bypass ? 0 : current_key(ui);
    draw_mask(ui,key!=0);
    if (key) {
        /* Ask the native completion path to finish a fully verified ordinary
         * queue. On any uncertainty retain the exact 0.4 one-line close path. */
        if (bulk_preflight(ui,key) && writable_byte((unsigned char *)ui+SUBTITLE_BULK_OFFSET))
            ((unsigned char *)ui)[SUBTITLE_BULK_OFFSET]=1;
        request_close(ui,1);
    }
}

/* First-open gate. Snapshot checks do not establish native lifetime or synchronization. */
static UpdateFn original_gate;
static volatile LONG gate_busy,gate_enabled;

typedef struct GateSnapshot {
    uint64_t manager, select, owner, resource, head, count, delta;
    uint64_t message[SUBTITLE_QUEUE_CAPACITY];
    PendingHudSnapshot pending;
} GateSnapshot;
static int early_filter(void *object,GateSnapshot *snapshot) {
 unsigned char *ui=object; uint64_t vt,mgr,st,sel,v,resource; uint32_t x,delta;
 SubtitleQueue queue;
 if(!readable_range(ui,SUBTITLE_READ_SIZE)||
  !read_u64(ui,0,&vt)||vt!=game_base+SUBTITLE_VT_RVA||ui[0xc]!=8||
  !read_u32(ui,UI_STATE_OFFSET,&x)||x||!read_u32(ui,0x2298,&x)||(x&0x30u)||
  !read_u64(ui,0x2100,&v)||v||!read_u32(ui,0x28f0,&x)||x||
  !read_u32(ui,0x68,&delta)||delta<=0x34000000u||delta>=0x7f800000u||
  !read_u64((void*)game_base,MANAGER_GLOBAL_RVA,&mgr)||!mgr||
  !read_u64((void*)(uintptr_t)mgr,0,&vt)||vt!=game_base+MANAGER_VT_RVA||
  !read_u64((void*)(uintptr_t)mgr,MANAGER_SUBTITLE_OFFSET,&st)||st!=(uintptr_t)ui||
  !safe_pending_hud(mgr,&snapshot->pending)||
  !read_u64((void*)(uintptr_t)mgr,MANAGER_SELECT_OFFSET,&sel)||!idle_select(sel)||
  !read_subtitle_queue(ui, &queue) || queue.count == 0) return 0;
 uint64_t owner, first_resource;
 const unsigned char *first_slot = ui + SUBTITLE_TEXT_ROWS_OFFSET + queue.head * SUBTITLE_TEXT_ROW_SIZE;
 if(!read_u64(first_slot,SUBTITLE_ROW_OWNER_OFFSET,&owner)||!owner||
  !readable_range((void*)(uintptr_t)owner,NPC_RESOURCE_READ_SIZE)||
  !read_u64((void*)(uintptr_t)owner,0,&vt)||vt!=game_base+NPC_VTABLE_RVA||
  !read_u64((void*)(uintptr_t)owner,NPC_RESOURCE_OFFSET,&first_resource))return 0;
 const Profile *profile=profile_for_resource((void*)(uintptr_t)first_resource);
 SubtitleQueueScan scan;
 if (!profile || !validate_subtitle_rows(ui, &queue, profile, owner,
     SUBTITLE_CHECK_FIRST_OPEN_DEADLINES, &scan)) return 0;
 for (uint64_t i = 0; i < queue.count; ++i) snapshot->message[i] = scan.messages[i];
 if(!readable_range((void*)(uintptr_t)owner,NPC_RESOURCE_READ_SIZE)||
  !read_u64((void*)(uintptr_t)owner,0,&vt)||vt!=game_base+NPC_VTABLE_RVA||
  !read_u64((void*)(uintptr_t)owner,NPC_RESOURCE_OFFSET,&resource)||
  !valid_resource((void*)(uintptr_t)resource,profile)||
  !read_u32((void*)(uintptr_t)owner,NPC_TALK_BUSY_OFFSET,&x)||x||
  !read_u64((void*)(uintptr_t)owner,NPC_TALK_CONTEXT_OFFSET,&v))return 0;
 if(v && (!readable_range((void*)(uintptr_t)v,0x22)||((unsigned char*)(uintptr_t)v)[0x21]))return 0;
 snapshot->manager=mgr; snapshot->select=sel; snapshot->owner=owner; snapshot->resource=resource;
 snapshot->head=queue.head; snapshot->count=queue.count; snapshot->delta=delta;
 /* Recheck key coordinates immediately before the caller's timer CAS. */
 uint64_t a,b,c,d;uint32_t delta2;PendingHudSnapshot pending_final={0};
 return read_u64(ui,SUBTITLE_QUEUE_HEAD_OFFSET,&a)&&a==queue.head&&read_u64(ui,SUBTITLE_QUEUE_COUNT_OFFSET,&b)&&b==queue.count&&
  read_u64(ui,SUBTITLE_TEXT_HEAD_OFFSET,&c)&&c==queue.head&&read_u64(ui,SUBTITLE_TEXT_COUNT_OFFSET,&d)&&d==queue.count&&
  read_u64((void*)(uintptr_t)mgr,MANAGER_SUBTITLE_OFFSET,&v)&&v==(uintptr_t)ui&&
  read_u64((void*)(uintptr_t)mgr,MANAGER_SELECT_OFFSET,&v)&&v==sel&&idle_select(sel)&&
  safe_pending_hud(mgr,&pending_final)&&equal_bytes((const unsigned char*)&snapshot->pending,(const unsigned char*)&pending_final,sizeof(pending_final))&&
  read_u32(ui,0x68,&delta2)&&delta2==delta&&read_u32(ui,UI_STATE_OFFSET,&x)&&!x&&
  writable_byte(ui+0x28f0)&&(((uintptr_t)(ui+0x28f0)&3)==0);
}
static void early_gate(void *ui,int bypass,uintptr_t caller) {
 if(InterlockedCompareExchange(&gate_busy,1,0)==0){
  GateSnapshot first={0},second={0};
  int eligible=0;
  __try { eligible=gate_enabled&&!bypass&&caller==game_base+GATE_CALLER_RVA&&!bypass_requested()&&
     early_filter(ui,&first)&&early_filter(ui,&second)&&
     equal_bytes((const unsigned char *)&first,(const unsigned char *)&second,sizeof(first))&&
     !bypass_requested(); }
  __except(EXCEPTION_EXECUTE_HANDLER) { eligible=0; }
  if(eligible) InterlockedCompareExchange((volatile LONG*)((unsigned char*)ui+0x28f0),0x3f000000,0);
  original_gate(ui);
  InterlockedExchange(&gate_busy,0);
 } else original_gate(ui);
}

static int supported_image(unsigned char *base) {
    if (!base) return 0;
    const IMAGE_DOS_HEADER *dos=(const IMAGE_DOS_HEADER *)base;
    if (dos->e_magic!=IMAGE_DOS_SIGNATURE || dos->e_lfanew<=0 ||
        dos->e_lfanew>0x100000) return 0;
    const IMAGE_NT_HEADERS64 *nt=(const IMAGE_NT_HEADERS64 *)(base+dos->e_lfanew);
    if (nt->Signature!=IMAGE_NT_SIGNATURE ||
        nt->FileHeader.Machine!=IMAGE_FILE_MACHINE_AMD64 ||
        nt->OptionalHeader.Magic!=IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt->FileHeader.TimeDateStamp!=PE_TIMESTAMP ||
        nt->OptionalHeader.SizeOfImage!=IMAGE_SIZE) return 0;
    static const unsigned char update_sig[]={
        0x40,0x53,0x48,0x83,0xEC,0x20,0x48,0x8B,0xD9,0x48,0x8B,0x89,
        0x40,0x3B,0,0,0xE8,0x9B,0xC1,0x5B,0,0x83,0xF8,1
    };
    static const unsigned char close_sig[]={
        0x44,0x8B,0x81,0x98,0x22,0,0,0x41,0x83,0xC8,0x20,0x41,0x8B,
        0xC0,0x41,0x0F,0xBA,0xF0,7,0x0F,0xBA,0xE8,7,0x84,0xD2,
        0x44,0x0F,0x45,0xC0,0x44,0x89,0x81
    };
    return equal_bytes(base+UI_UPDATE_RVA,update_sig,sizeof(update_sig)) &&
        equal_bytes(base+REQUEST_CLOSE_RVA,close_sig,sizeof(close_sig)) &&
        *(void **)(base+UI_SLOT_RVA)==base+UI_UPDATE_RVA;
}

static int supported_gate(unsigned char *base) {
    static const unsigned char gate_sig[]={0x40,0x53,0x48,0x83,0xec,0x20,0x48,0x83,0xb9,0x20,0x33,0,0,0};
    static const unsigned char cmp_sig[]={0x0f,0x2f,0x84,0xcb,0x24,0x29,0,0,0x73,0x1f};
    static const unsigned char call_sig[]={0xff,0x90,0x78,0x02,0,0};
    return equal_bytes(base+GATE_RVA,gate_sig,sizeof(gate_sig)) &&
       equal_bytes(base+0x1ebaa59,cmp_sig,sizeof(cmp_sig)) &&
       equal_bytes(base+0x1fb6f93,call_sig,sizeof(call_sig)) &&
       *(void **)(base+GATE_SLOT_RVA)==base+GATE_RVA;
}

void *animation_trampoline;
__declspec(dllexport) void SmithySkip_AnimationThunk(void);
#ifdef SMITHY_SKIP_TEST
static int animation_test_shift;
__declspec(dllexport) int SmithySkip_TestAnimationSignatures(void *base, int before_patch) {
    return animation_signatures_match(base,
        before_patch ? ANIMATION_WRAPPER_LENGTH : ANIMATION_WRAPPER_PREFIX_LENGTH);
}
__declspec(dllexport) void *SmithySkip_TestAnimationThunk(void){return (void*)SmithySkip_AnimationThunk;}
__declspec(dllexport) void SmithySkip_TestAnimationSetup(uintptr_t base,CrcFn crc,int shift){game_base=base;crc_override=crc;animation_test_shift=shift;}
__declspec(dllexport) void SmithySkip_TestAnimationTarget(void *target){animation_trampoline=target;}
__declspec(dllexport) float SmithySkip_TestAnimationDelta(void *ui,float dt,uintptr_t caller,int shift){float result=dt;__try{result=smithy_delta(ui,dt,caller,shift);}__except(EXCEPTION_EXECUTE_HANDLER){}return result;}
#endif
float SmithySkip_AnimationAdjust(void *ui,float dt,uintptr_t caller){
 float result=dt;
 __try {
#ifdef SMITHY_SKIP_TEST
 int shift=animation_test_shift;
#else
 int shift=0;
#endif
 result=smithy_delta(ui,dt,caller,shift);
 } __except(EXCEPTION_EXECUTE_HANDLER) {result=dt;}
 return result;
}
#ifdef SMITHY_SKIP_TEST
__declspec(dllexport) void SmithySkip_TestSetup(uintptr_t base,UpdateFn update,CloseFn close) {
    game_base=base;
    original_update=update;
    request_close=close;
    crc_override=0;
    hidden_gui=0;
    hidden_original_draw=0;
}
__declspec(dllexport) int SmithySkip_TestGateImage(unsigned char *base) { return supported_gate(base); }
__declspec(dllexport) void SmithySkip_TestGateSetup(UpdateFn gate) { original_gate=gate; gate_enabled=1; gate_busy=0; }
__declspec(dllexport) void SmithySkip_TestGate(void *ui,int bypass,uintptr_t caller) { early_gate(ui,bypass,caller); }
__declspec(dllexport) void SmithySkip_TestSetCRC(CrcFn crc) { crc_override=crc; }
__declspec(dllexport) int SmithySkip_TestPreflight(void *ui) {
    const Key *key=current_key(ui);
    return key ? bulk_preflight(ui,key) : 0;
}
__declspec(dllexport) const char *SmithySkip_TestKey(void *ui) {
    const Key *key=current_key(ui);
    return key ? key->name : 0;
}
__declspec(dllexport) void SmithySkip_TestTick(void *ui,int bypass) {
    process_tick(ui,bypass);
}
__declspec(dllexport) int SmithySkip_TestImage(unsigned char *base) {
    return supported_image(base);
}
#else
static PVOID volatile *hook_slot;
static PVOID volatile *gate_slot;

static void hooked_update(void *ui) {
    process_tick(ui,0);
}

static void hooked_gate(void *ui) {
    early_gate(ui,0,(uintptr_t)__builtin_return_address(0));
}

static void install_hook(void) {
    unsigned char *base=(unsigned char *)GetModuleHandleW(0);
    if (!supported_image(base)) {
        return;
    }
    PVOID volatile *slot=(PVOID volatile *)(base+UI_SLOT_RVA);
    DWORD protection,unused;
    if (!VirtualProtect((void *)slot,sizeof(PVOID),PAGE_READWRITE,&protection)) {
        return;
    }
    game_base=(uintptr_t)base;
    original_update=(UpdateFn)(base+UI_UPDATE_RVA);
    request_close=(CloseFn)(base+REQUEST_CLOSE_RVA);
    PVOID previous=InterlockedCompareExchangePointer(slot,(PVOID)hooked_update,
        (PVOID)original_update);
    VirtualProtect((void *)slot,sizeof(PVOID),protection,&unused);
    if (previous!=(PVOID)original_update) {
        return;
    }
    hook_slot=slot;
    /* Per-line close remains active if the optional first-open slot cannot be installed. */
    PVOID volatile *gs=(PVOID volatile *)(base+GATE_SLOT_RVA);
    if(!supported_gate(base)) return;
    original_gate=(UpdateFn)(base+GATE_RVA);
    if(!VirtualProtect((void*)gs,sizeof(PVOID),PAGE_READWRITE,&protection)) return;
    previous=InterlockedCompareExchangePointer(gs,(PVOID)hooked_gate,(PVOID)original_gate);
    if(!VirtualProtect((void*)gs,sizeof(PVOID),protection,&unused)) {
       if(previous==(PVOID)original_gate) InterlockedCompareExchangePointer(gs,(PVOID)original_gate,(PVOID)hooked_gate);
       return;
    }
    if(previous!=(PVOID)original_gate) return;
    gate_slot=gs;
    InterlockedExchange(&gate_enabled,1);
}

static DWORD WINAPI initialize_runtime_worker(void *unused) {
 (void)unused;
 HMODULE self;
 if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,(LPCWSTR)(uintptr_t)&initialize_runtime_worker,&self)) return 0;
 initialize_controller_input();
 unsigned char *base=(unsigned char*)GetModuleHandleW(0);
 if ((uintptr_t)base != game_base || !animation_signatures_match(base, ANIMATION_WRAPPER_LENGTH)) return 0;
 if(MH_Initialize()!=MH_OK)return 0;
 void *site=base+ANIMATION_PATCH_RVA;
 if(MH_CreateHook(site,(void*)SmithySkip_AnimationThunk,&animation_trampoline)!=MH_OK)return 0;
 if(MH_EnableHook(site)!=MH_OK){MH_RemoveHook(site);animation_trampoline=0;}
 return 0;
}

BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID reserved) {
    if (reason==DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        install_hook();
        if(hook_slot){HANDLE thread=CreateThread(NULL,0,initialize_runtime_worker,NULL,0,NULL);if(thread)CloseHandle(thread);}
    } else if (reason==DLL_PROCESS_DETACH && !reserved && hook_slot) {
        InterlockedExchange(&gate_enabled,0);
        if(gate_slot) { DWORD p,u; if(VirtualProtect((void*)gate_slot,sizeof(PVOID),PAGE_READWRITE,&p)){ InterlockedCompareExchangePointer(gate_slot,(PVOID)original_gate,(PVOID)hooked_gate); VirtualProtect((void*)gate_slot,sizeof(PVOID),p,&u); } }
        DWORD protection,unused;
        if (VirtualProtect((void *)hook_slot,sizeof(PVOID),PAGE_READWRITE,&protection)) {
            InterlockedCompareExchangePointer(hook_slot,(PVOID)original_update,(PVOID)hooked_update);
            VirtualProtect((void *)hook_slot,sizeof(PVOID),protection,&unused);
        }
    }
    return TRUE;
}
#endif







