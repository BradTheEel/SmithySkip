"""Run with the owned-test launcher after setting SMITHY_RESEARCH and building."""
import argparse
import ctypes as C
from functools import cache, partial
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import zlib


BASE = Path(__file__).resolve().parent
UPDATE = C.CFUNCTYPE(None, C.c_void_p)
CLOSE = C.CFUNCTYPE(None, C.c_void_p, C.c_ubyte)
CRC = C.CFUNCTYPE(C.c_uint32, C.c_char_p, C.c_uint32)
INPUT_DELTA = 0.125
ACCELERATED = (0.5, 0.5)
REJECTED = (0.0, INPUT_DELTA)
LANGUAGES = ('eng', 'rus')

# Offsets and serialized fields come from the independent Steam 15539686 fixture.
NPC_FIELDS = (
    (0, 8, 4), (4, 12, 1), (5, 13, 1), (6, 14, 1), (7, 15, 1), (8, 16, 1),
    (9, 18, 2), (11, 20, 2), (13, 24, 4), (17, 28, 4), (21, 32, 1),
    (22, 33, 1), (23, 34, 2), (25, 36, 1), (26, 37, 1), (27, 38, 1),
    (28, 39, 1), (29, 40, 2), (31, 44, 4), (35, 48, 4), (39, 52, 4),
    (43, 56, 4), (47, 60, 4),
)
ACTIVE_HASH = 0x28F4
BULK_BYTE = 0x3F23


def expect(actual, expected, context):
    if actual != expected:
        raise AssertionError(f'{context}: expected {expected!r}, got {actual!r}')


def address(buffer):
    return C.addressof(buffer)


def put(buffer, offset, fmt, value):
    struct.pack_into(fmt, buffer, offset, value)


def get(buffer, offset, fmt):
    return struct.unpack_from(fmt, buffer, offset)[0]


def hash_text(text):
    return zlib.crc32(text) ^ 0xFFFFFFFF


def bind_exports(path, baseline_bulk):
    dll = C.WinDLL(str(path))
    signatures = {
        'Setup': ([C.c_size_t, UPDATE, CLOSE], None),
        'GateSetup': ([UPDATE], None),
        'Gate': ([C.c_void_p, C.c_int, C.c_size_t], None),
        'AnimationSetup': ([C.c_size_t, CRC, C.c_int], None),
        'AnimationDelta': ([C.c_void_p, C.c_float, C.c_size_t, C.c_int], C.c_float),
        'Preflight': ([C.c_void_p], C.c_int),
        'Tick': ([C.c_void_p, C.c_int], None),
        'Key': ([C.c_void_p], C.c_char_p),
    }
    if not baseline_bulk:
        signatures['AnimationSignatures'] = ([C.c_void_p, C.c_int], C.c_int)
    for name, (argtypes, restype) in signatures.items():
        export = getattr(dll, f'SmithySkip_Test{name}')
        export.argtypes = argtypes
        export.restype = restype
    return dll


@cache
def load_dialogue(research, npc, lang):
    from inspect_dialog import gmd

    path = research / f'extracted/npc/npc{npc}'
    labels = gmd(path / f'text/npc{npc}_{lang}.gmd')
    packed = (path / f'data/npc{npc}.tk_npc').read_bytes()
    return labels, packed


class Fixture:
    def __init__(self, dll, research, npc, lang):
        self.dll = dll
        self.labels, packed = load_dialogue(research, npc, lang)
        self.rows = [C.create_string_buffer(64) for _ in self.labels]
        for i, row in enumerate(self.rows):
            entry = packed[10 + i * 51:10 + (i + 1) * 51]
            for src, dst, size in NPC_FIELDS:
                C.memmove(address(row) + dst, entry[src:src + size], size)
        self.ptrs = (C.c_void_p * len(self.rows))(*(address(row) for row in self.rows))

        # The RVA checks need a large address span, with only a few populated pages.
        self.image = mmap.mmap(-1, 0x51C4650)
        self.base = C.addressof(C.c_char.from_buffer(self.image))
        self.ui = C.create_string_buffer(0x3F30)
        self.mgr = C.create_string_buffer(0x14200)
        self.sel = C.create_string_buffer(0x2C40)
        self.npc = C.create_string_buffer(0x14680)
        self.resource = C.create_string_buffer(0xD0)

        resource_name = f'npc\\npc{npc}\\data\\npc{npc}'.encode() + b'\0'
        C.memmove(address(self.resource) + 12, resource_name, len(resource_name))
        put(self.resource, 0xA8, '<H', 500)
        put(self.resource, 0xB8, '<I', len(self.rows))
        put(self.resource, 0xC8, '<Q', address(self.ptrs))
        put(self.npc, 0, '<Q', self.base + 0x34A0D70)
        put(self.npc, 0x13C88, '<Q', address(self.resource))
        put(self.image, 0x51C4640, '<Q', address(self.mgr))
        put(self.mgr, 0, '<Q', self.base + 0x33F9190)
        put(self.mgr, 0x13E20, '<Q', address(self.ui))
        put(self.mgr, 0x13D60, '<Q', address(self.sel))
        put(self.sel, 0, '<Q', self.base + 0x3465270)
        put(self.ui, 0, '<Q', self.base + 0x34655E8)
        put(self.ui, 0xC, '<I', 8)
        put(self.ui, 0x68, '<f', 1 / 60)
        put(self.ui, 0x3E98, '<Q', address(self.npc))

        gate_calls = []
        self.gate_calls = gate_calls

        @UPDATE
        def gate(ui):
            gate_calls.append(ui)

        self.gate = gate
        self.dll.SmithySkip_TestGateSetup(gate)
        self.set_crc()

    def set_crc(self, action=None):
        @CRC
        def crc(text, seed):
            if seed != 0xFFFFFFFF:
                return 0
            if action is not None:
                action()
            return hash_text(text)

        self.crc = crc
        self.dll.SmithySkip_TestAnimationSetup(self.base, crc, 0)

    def set_count(self, count):
        put(self.ui, 0x3E90, '<Q', count)
        put(self.ui, 0x3320, '<Q', count)

    def text_slot(self, position):
        return 0x2918 + ((self.head + position) & 31) * 80

    def metadata_slot(self, position):
        return 0x3B88 + ((self.head + position) & 31) * 24

    def set_text(self, position, text):
        raw = text.encode()
        msg = C.create_string_buffer(8 + len(raw) + 1)
        put(msg, 4, '<I', len(raw))
        C.memmove(address(msg) + 8, raw, len(raw))
        self.messages.append(msg)
        put(self.ui, self.text_slot(position), '<Q', address(msg))

    def queue(self, indices, *, head=0, flags=None, active_index=None):
        self.head = head
        put(self.ui, 0x3E88, '<Q', head)
        put(self.ui, 0x3318, '<Q', head)
        self.set_count(len(indices))
        self.messages = []
        for position, index in enumerate(indices):
            self.set_text(position, self.labels[index][1])
            text = self.text_slot(position)
            meta = self.metadata_slot(position)
            put(self.ui, text + 0xC, '<f', 0.5 if position == 0 else 0)
            put(self.ui, text + 0x20, '<Q', address(self.npc))
            put(self.ui, meta, '<I', 0)
            put(self.ui, meta + 0x10, '<I', 0x11 if flags is None else flags[position])
        if active_index is None and indices:
            active_index = indices[0]
        active_hash = 0 if active_index is None else hash_text(self.labels[active_index][1].encode())
        put(self.ui, ACTIVE_HASH, '<I', active_hash)

    def hud_queue(self, count):
        self.hud = C.create_string_buffer(0x5900)
        put(self.hud, 0, '<Q', self.base + 0x3471398)
        put(self.hud, 0xC, '<I', 8)
        put(self.mgr, 0x14180, '<Q', address(self.hud))
        put(self.image, 0x33F9C90 + 0x10, '<Q', self.base + 0x1AD4F10)
        self.hud_args = [C.create_string_buffer(32) for _ in range(4)]
        if count == 1:
            self.hud_args[0] = self.npc
        for i in range(count):
            self.add_hud_task(i)

    def add_hud_task(self, position):
        offset = 0x4F60 + position * 0x80
        put(self.mgr, offset, '<Q', self.base + 0x33F9C90)
        put(self.mgr, offset + 8, '<Q', address(self.mgr))
        put(self.mgr, offset + 0x10, '<Q', address(self.hud_args[position]))
        put(self.mgr, offset + 0x38, '<Q', address(self.mgr) + offset)
        put(self.mgr, 0x8F60, '<Q', position + 1)

    def initial(self, shift=0):
        put(self.ui, 0x1D04, '<I', 0)
        put(self.ui, 0x28F0, '<f', 0)
        self.gate_calls.clear()
        self.dll.SmithySkip_TestGate(self.ui, shift, self.base + 0x1FB6F99)
        expect(self.gate_calls, [address(self.ui)], 'native gate callbacks')
        return get(self.ui, 0x28F0, '<f')

    def animation(self, shift=0):
        put(self.ui, 0x1D04, '<I', 1)
        return self.dll.SmithySkip_TestAnimationDelta(
            self.ui, INPUT_DELTA, self.base + 0x1FB7900, shift
        )

    def key(self):
        return self.dll.SmithySkip_TestKey(self.ui)

    def preflight(self):
        put(self.ui, 0x1D04, '<I', 1)
        return self.dll.SmithySkip_TestPreflight(self.ui)

    def check_tick(self, expected_bulk, shift, context):
        ui = self.ui
        events = []
        active_hash = get(ui, ACTIVE_HASH, '<I')
        put(ui, 0x1D04, '<I', 1)
        put(ui, ACTIVE_HASH, '<I', 0)

        @UPDATE
        def update(obj):
            events.append(('update', obj, get(ui, BULK_BYTE, '<B')))
            put(ui, ACTIVE_HASH, '<I', active_hash)

        @CLOSE
        def close(obj, flag):
            events.append(('close', obj, flag, get(ui, BULK_BYTE, '<B')))

        self.dll.SmithySkip_TestSetup(self.base, update, close)
        self.dll.SmithySkip_TestAnimationSetup(self.base, self.crc, 0)
        self.dll.SmithySkip_TestTick(ui, shift)
        expected_events = [('update', address(ui), 0)]
        if active_hash and not shift:
            expected_events.append(('close', address(ui), 1, expected_bulk))
        expect(events, expected_events, f'{context} tick callbacks')
        expect(get(ui, BULK_BYTE, '<B'), expected_bulk, f'{context} final bulk byte')


def check_acceleration(setup, name, expected, mutate=None):
    for branch, result in zip(('initial', 'animation'), expected, strict=True):
        fixture = setup()
        if mutate is not None:
            mutate(fixture)
        expect(getattr(fixture, branch)(), result, f'{name} {branch}')


def check_queue_matrix(make_fixture, audit, baseline_bulk):
    ordinary = audit['012']['entries'][0]['index']

    def make_queue(count=1, head=0, flags=None):
        fixture = make_fixture('012', 'rus')
        fixture.queue([ordinary] * count, head=head, flags=flags, active_index=ordinary)
        return fixture

    def case(name, expected, *, count=1, head=0, flags=None, mutate=None, shift=0):
        initial, animation, bulk = expected
        for branch, result in (('initial', initial), ('animation', animation), ('preflight', bulk)):
            fixture = make_queue(count, head, flags)
            if mutate is not None:
                mutate(fixture)
            value = fixture.preflight() if branch == 'preflight' else getattr(fixture, branch)(shift)
            expect(value, result, f'{name} {branch}')

        fixture = make_queue(count, head, flags)
        if mutate is not None:
            mutate(fixture)
        fixture.check_tick(0 if shift else bulk, shift, name)
        print(f'PASS {name} gate={initial} animation={animation} preflight={bulk}')

    case('valid kind=0 flags=0x11', (*ACCELERATED, 1))
    for row in (0, 1):
        count = row + 1
        flags = [0x11] * row + [0x12]
        expected = (*REJECTED, int(baseline_bulk))
        case(f'row {row + 1} flags=0x12', expected, count=count, flags=flags)
        case(
            f'row {row + 1} kind=1 flags=0x11', expected, count=count,
            mutate=lambda f: put(f.ui, f.metadata_slot(row), '<I', 1),
        )
    if baseline_bulk:
        return

    for flag in (0, 1, 0x10):
        case(f'native flag {flag:#04x}', (*ACCELERATED, int(bool(flag & 0x10))), flags=[flag])
    case('four unflagged rows', (*ACCELERATED, 0), count=4, flags=[0] * 4)
    case('empty queue with active ordinary key', (0.0, 0.5, 0), count=0)
    case('32 rows with wrapped head 29', (*ACCELERATED, 1),
         count=32, head=29, flags=[0x11] * 3 + [0] * 29)
    case('33 rows', (*REJECTED, 0), mutate=lambda f: f.set_count(33))
    case('broken flag prefix', (*REJECTED, 0), count=3, flags=[0x11, 0, 0x11])
    case('unknown flag 0x02', (*REJECTED, 0), flags=[0x02])
    case('unknown flag 0x31', (*REJECTED, 0), flags=[0x31, 0x11], count=2)
    case('Shift', (*REJECTED, 1), shift=1)

    for row in (0, 1):
        case(
            f'changed deadline on row {row + 1}', (0.0, 0.5, 1), count=2,
            mutate=lambda f: put(f.ui, f.text_slot(row) + 0xC, '<f', 0.25),
        )

    intro = audit['012']['introductions_preserved'][0]
    protected = lambda f: f.set_text(1, f.labels[intro][1])
    foreign = make_fixture('007', 'rus').labels[audit['007']['entries'][0]['index']][1]
    case('protected mixed', (*REJECTED, 0), count=2, mutate=protected)
    case('protected mixed unflagged', (*REJECTED, 0), count=2, flags=[0, 0], mutate=protected)
    case('unknown text', (*REJECTED, 0), count=2,
         mutate=lambda f: f.set_text(1, 'unrecognized distinct text'))
    case('cross-profile text', (*REJECTED, 0), count=2,
         mutate=lambda f: f.set_text(1, foreign))
    case('foreign owner', (*REJECTED, 0), count=2,
         mutate=lambda f: put(f.ui, f.text_slot(1) + 0x20, '<Q', address(f.sel)))

    two_rows = partial(make_queue, count=2)
    check_acceleration(two_rows, 'idle selector bit 5', ACCELERATED,
                       lambda f: put(f.sel, 0x2298, '<I', 0x20))
    selector_rejections = (
        ('pending choice', lambda f: put(f.sel, 0x2298, '<I', 0x80000000)),
        ('selector nonidle', lambda f: put(f.sel, 0x1D04, '<I', 1)),
        ('selector active flag', lambda f: put(f.sel, 0x2298, '<I', 1)),
        ('selector queued ring', lambda f: put(f.sel, 0x2C38, '<Q', 1)),
        ('selector callback', lambda f: put(f.sel, 0x29E0, '<Q', address(f.npc))),
        ('selector listener', lambda f: put(f.sel, 0x2100, '<Q', address(f.npc))),
        ('task queue nonzero', lambda f: put(f.mgr, 0x8F60, '<Q', 1)),
    )
    for name, mutate in selector_rejections:
        check_acceleration(two_rows, name, REJECTED, mutate)
    print('PASS idle selector and seven selector/task rejections')


def check_hud_tasks(make_fixture, audit):
    def setup(npc, count):
        fixture = make_fixture(npc, 'rus')
        flags = [0x11, 0] if count == 1 else [0x11, 0x11, 0]
        indices = [entry['index'] for entry in audit[npc]['entries'][:len(flags)]]
        fixture.queue(indices, flags=flags)
        put(fixture.sel, 0x2298, '<I', 0x20)
        fixture.hud_queue(count)
        return fixture

    one = partial(setup, '792', 1)
    three = partial(setup, '005', 3)
    check_acceleration(one, 'NPC792 one HUD task', ACCELERATED)
    check_acceleration(three, 'NPC005 three HUD tasks', ACCELERATED)
    rejections = (
        (one, 'selector active', lambda f: put(f.sel, 0x1D04, '<I', 1)),
        (one, 'selector pending choice', lambda f: put(f.sel, 0x2C38, '<Q', 1)),
        (one, 'selector listener', lambda f: put(f.sel, 0x2100, '<Q', address(f.npc))),
        (one, 'unknown task vtable', lambda f: put(f.mgr, 0x4F60, '<Q', f.base + 0x33F9C60)),
        (one, 'task owner mismatch', lambda f: put(f.mgr, 0x4F68, '<Q', address(f.npc))),
        (one, 'external task', lambda f: put(f.mgr, 0x4F98, '<Q', address(f.npc))),
        (one, 'additional incomplete task', lambda f: put(f.mgr, 0x8F60, '<Q', 2)),
        (one, 'wrong HUD vtable', lambda f: put(f.hud, 0, '<Q', f.base + 0x1234)),
        (one, 'HUD inactive', lambda f: put(f.hud, 0xC, '<I', 0)),
        (one, 'missing HUD', lambda f: put(f.mgr, 0x14180, '<Q', 0)),
        (one, 'HUD method changed', lambda f: put(f.image, 0x33F9CA0, '<Q', f.base + 0x1234)),
        (one, 'selector callback', lambda f: put(f.sel, 0x29E0, '<Q', address(f.npc))),
        (one, 'selector active flag with bit 5', lambda f: put(f.sel, 0x2298, '<I', 0x21)),
        (one, 'pending open queue', lambda f: put(f.mgr, 0xBD68, '<Q', 1)),
        (three, 'unknown middle task',
         lambda f: put(f.mgr, 0x4F60 + 0x80, '<Q', f.base + 0x33F9C60)),
        (three, 'four valid tasks', lambda f: f.add_hud_task(3)),
    )
    for control, name, mutate in rejections:
        check_acceleration(control, f'{name} positive control', ACCELERATED)
        check_acceleration(control, name, REJECTED, mutate)

    name = 'HUD argument changed during CRC'
    check_acceleration(three, f'{name} positive control', ACCELERATED)
    for branch, expected in zip(('initial', 'animation'), REJECTED, strict=True):
        fixture = three()
        manager = fixture.mgr
        offset = 0x4F70 + 0x80
        replacement = address(fixture.hud_args[3])
        fixture.set_crc(lambda: put(manager, offset, '<Q', replacement))
        expect(getattr(fixture, branch)(), expected, f'{name} {branch}')
        expect(get(manager, offset, '<Q'), replacement, f'{name} {branch} mutation')

    for npc in ('006', '702', '703', '704', '706', '711', '713', '714'):
        check_acceleration(partial(setup, npc, 3), f'Iceborne-shaped NPC{npc}', ACCELERATED)
    print('PASS HUD one/three tasks, 16 static rejections, both CRC mutations and 8 Iceborne profiles')


def check_animation_signatures(dll):
    call_rva = 0x1FB78F8
    wrapper_rva = 0x2353FE0
    # Frozen release byte sequences, independent of the candidate definitions.
    call = bytes.fromhex('48 8b cb e8 e0 c6 39 00')
    wrapper = bytes.fromhex(
        'f3 0f 10 89 c8 02 00 00 f3 0f 59 49 6c 48 8b 05 54 01 e7 02 '
        'f3 0f 5e 48 58 e9 72 03 00 00'
    )
    with mmap.mmap(-1, wrapper_rva + len(wrapper)) as image:
        image[call_rva:call_rva + len(call)] = call
        image[wrapper_rva:wrapper_rva + len(wrapper)] = wrapper
        base = C.addressof(C.c_char.from_buffer(image))

        def check(name, install, runtime):
            expect(dll.SmithySkip_TestAnimationSignatures(base, 1), install, f'{name} install')
            expect(dll.SmithySkip_TestAnimationSignatures(base, 0), runtime, f'{name} runtime')

        check('original signatures', 1, 1)
        for name, offset, runtime in (
            ('changed call', call_rva + 3, 0),
            ('changed wrapper prefix', wrapper_rva + 24, 0),
            ('changed wrapper patch position', wrapper_rva + 25, 1),
        ):
            image[offset] ^= 0xFF
            check(name, 0, runtime)
            image[offset] ^= 0xFF
    print('PASS animation signatures in install and runtime modes')


def check_exhaustive(make_fixture, audit):
    expect(len(audit), 23, 'profile count')
    outcomes = {branch: [0, 0] for branch in ('initial', 'animation', 'key')}
    for npc, profile in audit.items():
        allowed = {entry['index'] for entry in profile['entries']}
        for lang in LANGUAGES:
            # Reuse the immutable resource and image for this language. The queue
            # and phase fields are rebuilt for every tested dialogue line.
            fixture = make_fixture(npc, lang)
            expect(len(fixture.labels), profile['count'], f'NPC{npc} {lang} row count')
            for index, (label, _) in enumerate(fixture.labels):
                expected = index in allowed
                context = f'NPC{npc} {lang} row {index} ({label})'
                fixture.queue([index], flags=[0x11 if index & 1 else 0])
                initial = fixture.initial()
                animation = fixture.animation()
                key = bool(fixture.key())
                expect(initial, 0.5 if expected else 0.0, f'{context} initial')
                expect(animation, 0.5 if expected else INPUT_DELTA, f'{context} animation')
                expect(key, expected, f'{context} key')
                for branch, accepted in (
                    ('initial', initial == 0.5), ('animation', animation == 0.5), ('key', key),
                ):
                    outcomes[branch][0 if accepted else 1] += 1
            print(f'PASS exhaustive NPC{npc} {lang}')
    for branch, outcome in outcomes.items():
        expect(outcome, [3272, 1890], f'{branch} classification counts')
        print(f'PASS exhaustive {branch} allowed={outcome[0]} protected={outcome[1]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dll', type=Path, default=BASE / 'build/SmithySkipTest.dll')
    parser.add_argument('--audit', type=Path, default=BASE / 'build/profile-audit.json')
    parser.add_argument('--baseline-bulk', action='store_true',
                        help='run only the metadata controls against the rebuilt 0.6.5 DLL')
    args = parser.parse_args()
    research = Path(os.environ['SMITHY_RESEARCH']).resolve()
    sys.path.insert(0, str(research))
    audit = {profile['npc']: profile for profile in json.loads(args.audit.read_text(encoding='utf8'))}
    dll = bind_exports(args.dll.resolve(), args.baseline_bulk)
    make_fixture = partial(Fixture, dll, research)

    mode = 'frozen 0.6.5 bulk control' if args.baseline_bulk else 'candidate policy suite'
    print(f'Running {mode}', flush=True)
    check_queue_matrix(make_fixture, audit, args.baseline_bulk)
    if args.baseline_bulk:
        print('PASS baseline bulk accepts metadata rejected by gate and animation')
        return
    check_hud_tasks(make_fixture, audit)
    check_animation_signatures(dll)
    check_exhaustive(make_fixture, audit)


if __name__ == '__main__':
    main()
