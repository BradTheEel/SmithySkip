"""Exercise the shared hold-to-bypass policy with a synthetic XInput provider."""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import sys

from test_plugin import (
    ACCELERATED, ACTIVE_HASH, BASE, BULK_BYTE, CLOSE, REJECTED, UPDATE,
    Fixture, address, bind_exports, expect, get, put,
)


LB = 0x0100
RB = 0x0200
DEVICE_NOT_CONNECTED = 1167
GENERAL_FAILURE = 31
DRAW_FLAGS = 0x14


class Gamepad(C.Structure):
    _fields_ = [
        ('buttons', C.c_uint16), ('left_trigger', C.c_uint8), ('right_trigger', C.c_uint8),
        ('thumb_lx', C.c_int16), ('thumb_ly', C.c_int16),
        ('thumb_rx', C.c_int16), ('thumb_ry', C.c_int16),
    ]


class State(C.Structure):
    _fields_ = [('packet', C.c_uint32), ('gamepad', Gamepad)]


GET_STATE = C.WINFUNCTYPE(C.c_uint32, C.c_uint32, C.POINTER(State))


class FakeController:
    def __init__(self, dll):
        try:
            self.setup = dll.SmithySkip_TestControllerSetup
            self.set_time = dll.SmithySkip_TestControllerSetTime
        except AttributeError as exc:
            raise AssertionError('DLL is missing the controller bypass test interface') from exc
        self.setup.argtypes = [GET_STATE]
        self.setup.restype = None
        self.set_time.argtypes = [C.c_uint64]
        self.set_time.restype = None
        self.responses = {}
        self.calls = []
        self.get_state = GET_STATE(self.read_state)

    def set_slot(self, slot, *, buttons=0, left_trigger=0, right_trigger=0, result=0):
        self.responses[slot] = (result, Gamepad(buttons, left_trigger, right_trigger))

    def read_state(self, slot, output):
        self.calls.append(slot)
        result, gamepad = self.responses.get(slot, (DEVICE_NOT_CONNECTED, Gamepad()))
        # Return the fixture's output even on failure to exercise stale button bits.
        output[0] = State(len(self.calls), gamepad)
        return result


def check_controller(fixture, controller):
    ui = fixture.ui
    events = []
    active_hash = get(ui, ACTIVE_HASH, '<I')
    put(ui, DRAW_FLAGS, '<I', 0x12)

    @UPDATE
    def update(obj):
        events.append(('update', obj, get(ui, BULK_BYTE, '<B'), get(ui, DRAW_FLAGS, '<I')))
        put(ui, ACTIVE_HASH, '<I', active_hash)

    @CLOSE
    def close(obj, flag):
        events.append(('close', obj, flag, get(ui, BULK_BYTE, '<B')))

    def check_tick(context, bypass, shift):
        draw_before = get(ui, DRAW_FLAGS, '<I')
        events.clear()
        put(ui, ACTIVE_HASH, '<I', 0)
        put(ui, BULK_BYTE, '<B', 0)
        put(ui, 0x1D04, '<I', 1)
        fixture.dll.SmithySkip_TestTick(ui, shift)
        expected_events = [('update', address(ui), 0, draw_before)]
        if not bypass:
            expected_events.append(('close', address(ui), 1, 1))
        expect(events, expected_events, f'{context} tick callbacks')
        expect(get(ui, BULK_BYTE, '<B'), 0 if bypass else 1, f'{context} bulk byte')
        expect(get(ui, DRAW_FLAGS, '<I'), 0x12 if bypass else 0x10, f'{context} draw flags')

    fixture.dll.SmithySkip_TestSetup(fixture.base, update, close)
    fixture.dll.SmithySkip_TestAnimationSetup(fixture.base, fixture.crc, 0)

    def check_hooks(context, *, bypass=False, shift=0):
        timer, delta = REJECTED if bypass else ACCELERATED
        # The synthetic native close leaves the previous scenario's latch set.
        put(ui, BULK_BYTE, '<B', 0)
        expect(fixture.initial(shift), timer, f'{context} first-open timer')
        expect(fixture.animation(shift), delta, f'{context} animation delta')
        check_tick(context, bypass, shift)
        print(f'PASS {context}')

    check_hooks('no XInput provider')
    controller.setup(controller.get_state)
    try:
        controller.set_slot(0, buttons=RB)
        check_hooks('held RB restores drawing and bypasses all three hooks', bypass=True)
        controller.set_slot(0)
        check_hooks('released RB resumes skipping')
        controller.set_slot(0, buttons=LB)
        check_hooks('LB alone leaves skipping enabled')
        controller.set_slot(0, left_trigger=255, right_trigger=255)
        check_hooks('analog triggers leave skipping enabled')
        check_hooks('Shift still bypasses all three hooks', bypass=True, shift=1)

        controller.set_time(1000)
        controller.set_slot(3, buttons=RB | LB)
        check_hooks('RB held alongside LB on slot 3 bypasses', bypass=True)

        controller.set_slot(0, buttons=RB, result=GENERAL_FAILURE)
        controller.set_slot(3, buttons=RB, result=DEVICE_NOT_CONNECTED)
        check_hooks('failed and disconnected slots ignore stale RB bits')
        calls_after_failure = len(controller.calls)
        controller.set_time(1999)
        controller.set_slot(3, buttons=RB)
        check_hooks('failed slots stay unpolled during cooldown')
        expect(len(controller.calls), calls_after_failure, 'poll count before retry deadline')
        controller.set_time(2000)
        check_hooks('slot 3 bypasses again on reconnect at the retry deadline', bypass=True)

        controller.setup(controller.get_state)
        controller.responses.clear()
        controller.set_slot(0)
        crc_calls = []

        def press_during_crc():
            crc_calls.append(True)
            controller.set_slot(0, buttons=RB)

        fixture.set_crc(press_during_crc)
        expect(fixture.initial(), 0.0, 'RB pressed during CRC prevents first-open timer write')
        expect(bool(crc_calls), True, 'first-open CRC callback ran')
        fixture.set_crc()
        print('PASS first-open rechecks RB before writing the timer')
    finally:
        controller.setup(GET_STATE())
    check_hooks('provider removal restores skipping')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dll', type=Path, default=BASE / 'build/SmithySkipTest.dll')
    parser.add_argument('--audit', type=Path, default=BASE / 'build/profile-audit.json')
    args = parser.parse_args()
    dll = bind_exports(args.dll.resolve(), False)
    controller = FakeController(dll)
    research = Path(os.environ['SMITHY_RESEARCH']).resolve()
    sys.path.insert(0, str(research))
    audit = {profile['npc']: profile for profile in json.loads(args.audit.read_text(encoding='utf8'))}
    fixture = Fixture(dll, research, '012', 'rus')
    fixture.queue([audit['012']['entries'][0]['index']])
    check_controller(fixture, controller)


if __name__ == '__main__':
    main()
