import math
import time
import rtmidi
import asyncio
import simpleobsws

parameters = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False)
parameters.eventSubscriptions = (1 << 3) | (1 << 16)
ws = simpleobsws.WebSocketClient(url='ws://localhost:4455', password='test', identification_parameters=parameters)

midi_out = rtmidi.MidiOut()
for idx, port in enumerate(midi_out.get_ports()):
    if "X-Touch-Ext" in port:
        midi_out.open_port(idx)
        print('OUT Port opened:', midi_out.is_port_open(), midi_out.get_port_name(idx))
        break

midi_in = rtmidi.MidiIn()
for idx, port in enumerate(midi_in.get_ports()):
    if "X-Touch-Ext" in port:
        midi_in.open_port(idx)
        print('IN Port opened:', midi_in.is_port_open(), midi_in.get_port_name(idx))
        break


obs_inputs = {
    0: {"name": "CANCEL", "id": "0"},
    1: {"name": "RESET", "id": "1"}
}


async def filter_audio_inputs(my_reqs):
    global obs_inputs

    ret = await ws.call_batch(my_reqs, halt_on_failure=False)

    for idx, result in enumerate(ret, 2):
        if not result.ok():
            obs_inputs.pop(idx)

    obs_inputs = {new_key: value for new_key, (old_key, value) in enumerate(obs_inputs.items())}


async def obs_request(req, data=None):

    if data is None:
        request = simpleobsws.Request(req)
    else:
        request = simpleobsws.Request(req, data)

    ret = await ws.call(request)

    return ret.responseData


class Strip:

    led_modes = {
        0: (1, 11),
        1: (17, 27),
        2: (65, 75),
        3: (81, 91),
    }

    colors = {1: "RED", 2: "GREEN", 3: "YELLOW", 4: "BLUE", 5: "MAGENTA", 6: "CYAN", 7: "WHITE", 8: "BLACK"}

    def __init__(self, num):
        self.num = num
        self.enc_mode = 3
        self.enc_value = -81
        self.rec = 0
        self.solo = 0
        self.mute = 0
        self.select = 0
        self.color_cnt = 7
        self.color_idx = 7
        self.option = 0
        self.source_name = ""
        self.source_uuid = ""
        self.source_cnt = 0
        self.source_idx = 0
        self.fader_current = 0
        self.fader_busy = 0
        self.fader_delta = 0

    def reset(self):
        # reset internal variables
        self.enc_mode = 3
        self.enc_value = -81
        self.rec = 0
        self.solo = 0
        self.mute = 0
        self.select = 0
        self.color_cnt = 7
        self.color_idx = 7
        self.option = 0
        self.source_name = ""
        self.source_uuid = ""
        self.source_cnt = 0
        self.source_idx = 0
        self.fader_current = 0
        self.fader_busy = 0
        self.fader_delta = 0

        # reset LCD color
        self.change_lcd_color(self.color_idx)

        # reset LCD text
        self.write_text(0, "")
        self.write_text(1, "")

        # power off encoder leds
        midi_out.send_message([176, self.num + 48, 0])

        # power off buttons
        midi_out.send_message([144, self.num, 0])
        midi_out.send_message([144, self.num + 8, 0])
        midi_out.send_message([144, self.num + 16, 0])

        # reset fader
        midi_out.send_message([self.num + 224, 1, 0])

    def restore(self):
        # restore internal variables (counters)
        self.source_cnt = self.source_idx
        self.color_cnt = self.color_idx
        self.select = 0

        # restore text
        self.write_text(0, self.source_name)
        self.write_text(1, "")

        # restore LCD color
        self.change_lcd_color(self.color_idx)

        # restore buttons leds
        midi_out.send_message([144, self.num, self.rec * 127])
        midi_out.send_message([144, self.num + 8, self.solo * 127])
        midi_out.send_message([144, self.num + 16, self.mute * 127])
        midi_out.send_message([144, self.num + 24, self.select])

        # restore encoder leds
        final_value = self.enc_value + self.led_modes[self.enc_mode][0]
        midi_out.send_message([176, self.num + 48, final_value])

        # restore fader
        midi_out.send_message([self.num + 224, 1, self.fader_current])

    async def process_button(self, msg):

        button = msg[0]
        value = msg[1]

        if button == self.num:  # REC button TRACK
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.rec = 1 - self.rec
                        midi_out.send_message([144, self.num, self.rec * 127])
                        await obs_request("SetInputAudioTracks", {"inputUuid": self.source_uuid, "inputAudioTracks": {"2": bool(self.rec)}})

        elif button == self.num + 8:  # SOLO button
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.solo = 1 - self.solo
                        if self.solo == 1:
                            monitor_type = "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"
                        else:
                            monitor_type = "OBS_MONITORING_TYPE_NONE"

                        midi_out.send_message([144, self.num + 8, self.solo * 127])
                        await obs_request("SetInputAudioMonitorType", {"inputUuid": self.source_uuid, "monitorType": monitor_type})

        elif button == self.num + 16:  # MUTE button
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.mute = 1 - self.mute
                        midi_out.send_message([144, self.num + 16, self.mute * 127])
                        await obs_request("SetInputMute", {"inputUuid": self.source_uuid, "inputMuted": bool(self.mute)})

        elif button == self.num + 24:  # SELECT button
            if value == 127:
                # todo: listen to OBS EVENTS and cancel selection if sources changed while selecting
                # restore all the other strips
                for strip in strips.values():
                    if strip.num != self.num:
                        strip.restore()

                # change select status
                self.select = 1 - self.select
                midi_out.send_message([144, self.num + 24, self.select])

                if self.select == 1:
                    # power off encoder leds
                    midi_out.send_message([176, self.num + 48, 0])

                    # power off buttons leds
                    midi_out.send_message([144, self.num, 0])
                    midi_out.send_message([144, self.num + 8, 0])
                    midi_out.send_message([144, self.num + 16, 0])

                    # get sources from obs
                    req_list = []
                    res = await obs_request("GetInputList")
                    for idx, inpt in enumerate(res["inputs"], 2):
                        obs_inputs[idx] = {"name": inpt["inputName"], "id": inpt["inputUuid"]}
                        req_list.append(simpleobsws.Request('GetInputAudioMonitorType', {"inputUuid": inpt["inputUuid"]}, ))
                    await filter_audio_inputs(req_list)

                    # update LCD text
                    if self.option == 0:
                        self.write_text(0, "SOURCE")
                        self.write_text(1, obs_inputs[self.source_idx]["name"])
                    else:
                        self.write_text(0, "COLOR")
                        self.write_text(1, self.colors[self.color_idx])

                elif self.select == 0:

                    if self.option == 0:

                        # get current selection
                        source_selected_name = obs_inputs[self.source_cnt]["name"]
                        source_selected_idx = obs_inputs[self.source_cnt]["id"]

                        if source_selected_name == "CANCEL":
                            self.restore()

                        elif source_selected_name == "RESET":
                            self.reset()

                        else:
                            if self.source_uuid != source_selected_idx:
                                for strip in strips.values():
                                    if strip.source_uuid == source_selected_idx:
                                        self.color_cnt = strip.color_idx
                                        self.color_idx = strip.color_idx
                                        self.enc_mode = strip.enc_mode

                            if self.source_uuid != source_selected_idx:
                                # get OBS states to update button states
                                current_solo = await obs_request("GetInputAudioMonitorType", {"inputUuid": source_selected_idx})
                                current_solo = current_solo["monitorType"]
                                current_mute = await obs_request("GetInputMute", {"inputUuid": source_selected_idx})
                                current_mute = current_mute["inputMuted"]
                                current_balance = await obs_request("GetInputAudioBalance", {"inputUuid": source_selected_idx})
                                current_balance = current_balance["inputAudioBalance"] * 10  # instead my_map, casually the ranges are the same x10
                                current_slider = await obs_request("GetInputVolume", {"inputUuid": source_selected_idx})
                                current_slider = current_slider["inputVolumeMul"] ** (1 / 3)
                                current_slider = int(my_map(current_slider, 0, 1, 0, 127))
                                current_track = await obs_request("GetInputAudioTracks", {"inputUuid": source_selected_idx})
                                current_track = int(current_track["inputAudioTracks"]["2"])

                                # update internal variables
                                if current_solo == "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT":
                                    self.solo = 1
                                else:
                                    self.solo = 0

                                self.enc_value = current_balance
                                self.rec = current_track
                                self.mute = int(current_mute)
                                self.fader_current = current_slider
                                self.source_name = source_selected_name
                                self.source_uuid = source_selected_idx
                                self.source_idx = self.source_cnt

                            # update LCD Text
                            self.write_text(0, self.source_name)
                            self.write_text(1, "")

                            # update LCD color
                            self.change_lcd_color(self.color_idx)
                            self.color_cnt = self.color_idx

                            # update buttons leds
                            midi_out.send_message([144, self.num, self.rec * 127])
                            midi_out.send_message([144, self.num + 8, self.solo * 127])
                            midi_out.send_message([144, self.num + 16, self.mute * 127])

                            # update encoder leds
                            final_value = self.enc_value + self.led_modes[self.enc_mode][0]
                            midi_out.send_message([176, self.num + 48, final_value])

                            # update fader
                            midi_out.send_message([self.num + 224, 1, self.fader_current])

                            # reset strips that previously have the current selection
                            for strip in strips.values():
                                if strip.source_uuid == source_selected_idx and strip.num != self.num:
                                    strip.reset()

                    elif self.option == 1:

                        self.color_idx = self.color_cnt
                        self.restore()

        elif button == self.num + 32:  # ENCODER button
            if value == 127:

                if self.select == 0:
                    if self.source_idx != 0:
                        # update encoder mode
                        self.enc_mode = self.enc_mode + 1
                        if self.enc_mode > (len(self.led_modes) - 1):
                            self.enc_mode = 0

                        # update encoder lights
                        final_value = self.enc_value + self.led_modes[self.enc_mode][0]
                        midi_out.send_message([176, self.num + 48, final_value])

                elif self.select == 1:

                    self.option = 0 ** self.option

                    if self.option == 0:
                        self.write_text(0, "SOURCE")
                        self.write_text(1, obs_inputs[self.source_cnt]["name"])
                    else:
                        self.write_text(0, "COLOR")
                        self.write_text(1, self.colors[self.color_cnt])

        else:
            print("TOUCH", self.num)

    async def process_encoder(self, msg):

        if self.select == 0:
            if self.source_idx != 0:

                if msg[1] < 50:
                    self.enc_value = self.enc_value + 1
                    if self.enc_value > 10:
                        self.enc_value = 10

                elif msg[1] > 50:
                    self.enc_value = self.enc_value - 1
                    if self.enc_value < 0:
                        self.enc_value = 0

                await obs_request("SetInputAudioBalance", {"inputUuid": self.source_uuid, "inputAudioBalance": self.enc_value / 10})

        if self.select == 1:

            if msg[1] < 50:
                if self.option == 0:
                    self.source_cnt = self.source_cnt + 1
                    if self.source_cnt > (len(obs_inputs) - 1):
                        self.source_cnt = len(obs_inputs) - 1
                    self.write_text(0, "SOURCE")
                    self.write_text(1, obs_inputs[self.source_cnt]["name"])
                elif self.option == 1:
                    self.color_cnt = self.color_cnt + 1
                    if self.color_cnt > 8:
                        self.color_cnt = 1
                    self.write_text(0, "COLOR")
                    self.write_text(1, self.colors[self.color_cnt])
                    self.change_lcd_color(self.color_cnt)

            elif msg[1] > 50:
                if self.option == 0:
                    self.source_cnt = self.source_cnt - 1
                    if self.source_cnt < 0:
                        self.source_cnt = 0
                    self.write_text(0, "SOURCE")
                    self.write_text(1, obs_inputs[self.source_cnt]["name"])
                else:
                    self.color_cnt = self.color_cnt - 1
                    if self.color_cnt < 1:
                        self.color_cnt = 8
                    self.write_text(0, "COLOR")
                    self.write_text(1, self.colors[self.color_cnt])
                    self.change_lcd_color(self.color_cnt)

    async def process_fader(self, msg):

        if self.source_name != "":
            if self.select == 0:

                self.fader_current = msg[1]
                self.fader_delta = time.time_ns()
                self.fader_busy = 1

                fader_percentage = my_map(msg[1], 0, 127, 0, 1)
                slider_mul = fader_percentage ** 3
                req = simpleobsws.Request("SetInputVolume", {"inputUuid": self.source_uuid, "inputVolumeMul": slider_mul})
                await ws.emit(req)

    def pos_fader(self):
        midi_out.send_message([self.num + 224, 1, self.fader_current])
        self.fader_busy = 0

    def write_text(self, line, my_str):

        if not (0 <= line <= 1):
            print("wrong LCD line")
            return

        my_str = my_str[:7]

        # Clear LCD text
        midi_out.send_message([
            0xF0,  # MIDI System Exclusive Start
            0x00, 0x00, 0x66,  # Header of Mackie Control Protocol
            0x15,  # Device vendor ID
            0x12,  # Command: Update LCD
            0x00 + (7 * self.num) + (56 * line),  # Offset (starting position in LCD) 0x00 to 0x37 for the upper line and 0x38 to 0x6F for the lower line
            0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,  # Chars to display in UTF-16
            0xF7  # MIDI System Exclusive End
        ])

        # write LCD text
        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x12, 0x00 + (7 * self.num) + (56 * line)]
        text = [ord(char) for char in my_str]
        payload.extend(text)
        payload.append(0xF7)
        midi_out.send_message(payload)

    def change_lcd_color(self, clr):

        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x72]

        for _, strip in strips.items():
            payload.append(strip.color_idx)

        payload.append(0xF7)

        payload[self.num + 6] = clr

        midi_out.send_message(payload)

    def update_volumeter(self, obs_event_data):

        if self.select == 0:

            average_mul = [channel[1] for channel in obs_event_data]
            average_mul = sum(average_mul) / len(average_mul)

            if average_mul > 0:

                current_peak_db = 20 * math.log10(average_mul)

                if current_peak_db < -60:
                    current_peak_db = -60
                elif current_peak_db > -4:
                    current_peak_db = 0

                midi_value = my_map(current_peak_db, -60, 0, 0, 14)
                midi_out.send_message([208, (self.num * 16 + midi_value), 0])

    def update_fader(self, obs_event_data):

        if self.fader_busy:
            return

        slider_percentage = obs_event_data["inputVolumeMul"] ** (1 / 3)
        self.fader_current = int(my_map(slider_percentage, 0, 1, 0, 127))

        midi_out.send_message([self.num + 224, 1, self.fader_current])

    def update_mute(self, obs_event_data):
        if self.select == 0:
            self.mute = int(obs_event_data["inputMuted"])
            midi_out.send_message([144, self.num + 16, self.mute * 127])

    def update_track(self, obs_event_data):
        if self.select == 0:
            self.rec = int(obs_event_data["inputAudioTracks"]["2"])
            midi_out.send_message([144, self.num, self.rec * 127])

    def update_balance(self, obs_event_data):
        if self.select == 0:
            val = int(round(obs_event_data["inputAudioBalance"], 1) * 10)
            self.enc_value = val
            final_value = self.enc_value + self.led_modes[self.enc_mode][0]
            midi_out.send_message([176, self.num + 48, final_value])

    def update_monitor(self, obs_event_data):
        if self.select == 0:
            if obs_event_data["monitorType"] == "OBS_MONITORING_TYPE_NONE":
                self.solo = 0
            else:
                self.solo = 1
            midi_out.send_message([144, self.num + 8, self.solo * 127])


strips = {
    0: Strip(0),
    1: Strip(1),
    2: Strip(2),
    3: Strip(3),
    4: Strip(4),
    5: Strip(5),
    6: Strip(6),
    7: Strip(7)
}


def my_map(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


async def obs_volumeter_callback(event_data):
    for source in event_data["inputs"]:
        for strip in strips.values():
            if source["inputUuid"] == strip.source_uuid:
                # Ignore empty lists
                if source["inputLevelsMul"]:
                    strip.update_volumeter(source["inputLevelsMul"])
                break


# todo identify event to merge all callbacks
async def obs_slider_callback(event_data):
    for strip in strips.values():
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_fader(event_data)
            break


async def obs_mute_callback(event_data):
    for strip in strips.values():
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_mute(event_data)
            break


async def obs_track_callback(event_data):
    for strip in strips.values():
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_track(event_data)
            break


async def obs_balance_callback(event_data):
    for strip in strips.values():
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_balance(event_data)
            break


async def obs_monitor_callback(event_data):
    for strip in strips.values():
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_monitor(event_data)
            break


async def main():

    try:
        await ws.connect()
        await ws.wait_until_identified()
    except Exception as e:
        print(e)
        loop.stop()

    ws.register_event_callback(obs_balance_callback, "InputAudioBalanceChanged")
    ws.register_event_callback(obs_track_callback, "InputAudioTracksChanged")
    ws.register_event_callback(obs_monitor_callback, "InputAudioMonitorTypeChanged")
    ws.register_event_callback(obs_mute_callback, "InputMuteStateChanged")
    ws.register_event_callback(obs_volumeter_callback, "InputVolumeMeters")
    ws.register_event_callback(obs_slider_callback, "InputVolumeChanged")

    fader_timeout = 0.3

    # reset all strips
    for strip in strips.values():
        strip.reset()

    while True:

        current = time.time_ns()
        for strip in strips.values():
            if strip.fader_busy and current - strip.fader_delta > fader_timeout * 1000000000:
                strip.pos_fader()
                await asyncio.sleep(0)

        midi_msg = midi_in.get_message()
        if not midi_msg:
            await asyncio.sleep(0)
            continue
        b1 = midi_msg[0][0]
        b2 = midi_msg[0][1]
        b3 = midi_msg[0][2]

        if b1 == 144:
            strip = strips[b2 % 8]
            await strip.process_button([b2, b3])
        elif b1 == 176:
            strip = strips[b2 % 8]
            await strip.process_encoder([b2, b3])
        else:
            strip = strips[b1 - 224]
            await strip.process_fader([b1, b3])

        await asyncio.sleep(0)

    await ws.disconnect()

# todo implement RTP-MIDI (ethernet) protocol
loop = asyncio.get_event_loop()
loop.create_task(main())

loop.run_forever()
