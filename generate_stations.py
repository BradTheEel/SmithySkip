"""Hash-only policy for explicitly selected service resource tables."""
import json
import os
from pathlib import Path
import struct
import sys
import zlib

from config import NPCS

BASE = Path(__file__).resolve().parent


def crc(text):
    return zlib.crc32(text.encode('utf-8')) ^ 0xffffffff


def fnv1a(data):
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xffffffffffffffff
    return h


def resolve_intro_indices(
    npc: str, labels: list[tuple[str, str]], intro_keys: tuple[str, ...]
) -> list[int]:
    indices = []
    seen = set()
    for key in intro_keys:
        if key in seen:
            raise ValueError(f'NPC{npc}: duplicate configured introduction key {key!r}')
        seen.add(key)
        matches = [index for index, (label, _) in enumerate(labels) if label == key]
        if not matches:
            raise ValueError(f'NPC{npc}: missing introduction key {key!r}')
        if len(matches) != 1:
            raise ValueError(f'NPC{npc}: ambiguous introduction key {key!r}')
        indices.append(matches[0])
    return indices


def main():
    # Supply the separately obtained research inputs; they are not distributed.
    research = Path(os.environ['SMITHY_RESEARCH']).resolve()
    sys.path.insert(0, str(research))
    try:
        from inspect_dialog import gmd
    finally:
        sys.path.pop(0)

    out = ['/* Generated localized-text CRC policy; no localized dialogue text. */']
    profiles = []
    for npc, (role, intro_keys) in NPCS.items():
        path = research / f'extracted/npc/npc{npc}'
        data = (path / f'data/npc{npc}.tk_npc').read_bytes()
        labels = gmd(path / f'text/npc{npc}_eng.gmd')
        rus = gmd(path / f'text/npc{npc}_rus.gmd')
        if [key for key, _ in labels] != [key for key, _ in rus]:
            raise ValueError(f'NPC{npc}: English and Russian GMD key sequences differ')
        if len(data) < 10:
            raise ValueError(f'NPC{npc}: truncated .tk_npc header')
        count = struct.unpack_from('<I', data, 6)[0]
        if count != len(labels) or len(data) != 10 + count * 51:
            raise ValueError(f'NPC{npc}: GMD row count or .tk_npc size disagrees with .tk_npc count {count}')

        introductions = resolve_intro_indices(npc, labels, intro_keys)
        hashes = [(crc(a[1]), crc(b[1])) for a, b in zip(labels, rus)]
        eligible = {
            i for i in range(count)
            if data[10 + i * 51 + 4] in (1, 4) and i not in introductions
        }
        protected = {h for i, pair in enumerate(hashes) if i not in eligible for h in pair}
        ambiguous = []
        while True:
            conflicts = {i for i in eligible if protected.intersection(hashes[i])}
            if not conflicts:
                break
            for i in conflicts:
                protected.update(hashes[i])
            ambiguous.extend(sorted(conflicts))
            eligible -= conflicts

        entries = []
        out.append(f'static const Key keys_{npc}[] = {{')
        for i, (key, _) in enumerate(labels):
            if i not in eligible:
                continue
            kind = data[10 + i * 51 + 4]
            if not any(tag in key for tag in ('_kihon_', '_sisetu_', '_seken_', '_sinkou_')):
                raise ValueError(f'NPC{npc}: unexpected selected key {key!r} at row {i}')
            eng, russian = hashes[i]
            entries.append({'index': i, 'key': key, 'hash_eng': eng, 'hash_rus': russian, 'kind': kind})
            out.append(f'    {{UINT32_C(0x{eng:08X}), UINT32_C(0x{russian:08X}), "{key}"}},')
        if not entries:
            raise ValueError(f'NPC{npc}: no eligible GMD entries')
        out.append('};')
        profiles.append({
            'npc': npc,
            'role': role,
            'count': count,
            'fingerprint': fnv1a(data[10:]),
            'introductions_preserved': introductions,
            'ambiguous_preserved': ambiguous,
            'entries': entries,
        })

    out.append('static const Profile profiles[] = {')
    for p in profiles:
        npc = p['npc']
        out.append(
            f'    {{"npc/npc{npc}/data/npc{npc}", "NPC{npc}", {p["count"]}, '
            f'UINT64_C(0x{p["fingerprint"]:016X}), keys_{npc}, sizeof(keys_{npc})/sizeof(keys_{npc}[0])}},'
        )
    out.append('};')

    (BASE / 'src/profiles.h').write_text('\n'.join(out) + '\n')
    (BASE / 'build').mkdir(exist_ok=True)
    (BASE / 'build/profile-audit.json').write_text(
        json.dumps(profiles, indent=2, ensure_ascii=False) + '\n', encoding='utf8'
    )
    for p in profiles:
        print(p['npc'], len(p['entries']), 'selected;', p['count'] - len(p['entries']),
              'preserved; ambiguous', p['ambiguous_preserved'])
    print('TOTAL:', len(profiles), 'tables;', sum(len(p['entries']) for p in profiles),
          'selected messages;', sum(p['count'] - len(p['entries']) for p in profiles), 'preserved')


if __name__ == '__main__':
    main()
