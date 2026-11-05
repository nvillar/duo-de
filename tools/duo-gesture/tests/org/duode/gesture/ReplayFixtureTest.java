/*
 * Copyright (C) 2026 The Duo-DE Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.duode.gesture;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.android.systemui.duo.gesture.DuoThreeFingerClassifier.Config;
import com.android.systemui.duo.gesture.DuoThreeFingerClassifier.Result;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * Replays the raw Duo 1 captures in testdata/ (see testdata/README.md for what each touch is) and checks what the
 * classifier reports for every touch.
 */
@RunWith(JUnit4.class)
public class ReplayFixtureTest {
    private static final Config DEFAULTS = Config.forDensity(2.5f);
    /** Same zone everywhere, so that the outcome cannot depend on which panel edge is the display top. */
    private static final Config UNIFORM_ZONES = Config.parse("topDp=24,bottomDp=24", DEFAULTS);

    private static Path testdata(String name) {
        return Paths.get(System.getProperty("duo.gesture.testdata", "tools/duo-gesture/testdata"), name);
    }

    private static List<GeteventReplay.Touch> replay(String name, Config config, int rotation) throws IOException {
        try (Reader r = Files.newBufferedReader(testdata(name), StandardCharsets.UTF_8)) {
            return new GeteventReplay(config, rotation).replay(r);
        }
    }

    /** "touch EVENT GESTURE target>to split reason" per result. */
    private static List<String> summary(List<GeteventReplay.Touch> touches) {
        List<String> out = new ArrayList<>();
        for (int i = 0; i < touches.size(); i++) {
            for (Result r : touches.get(i).results) {
                String t = r.target < 0 ? "" : " " + r.target + (r.to >= 0 ? ">" + r.to : "");
                out.add(i + " " + r.event + " " + r.gesture + t + " " + r.onPanel0 + "+" + r.onPanel1 + " "
                        + r.reason);
            }
        }
        return out;
    }

    @Test
    public void singlePanelPinchesAndAPushAcrossTheHinge() throws IOException {
        List<GeteventReplay.Touch> touches = replay("touch3.getevent", DEFAULTS, 0);
        assertEquals(12, touches.size());
        // Touch 0 includes three parked contacts at y=0 whose new tracking ids resend only X.
        assertEquals(6, touches.get(0).contacts);
        assertEquals(Arrays.asList(
                "0 RECOGNIZED SPAN 0 3+0 pinch", "0 COMMIT SPAN 0 3+0 parked",
                "1 RECOGNIZED CONTRACT 0 3+0 pinch", "1 COMMIT CONTRACT 0 3+0 lift",
                "2 RECOGNIZED SPAN 1 0+3 pinch", "2 COMMIT SPAN 1 0+3 lift",
                "3 RECOGNIZED CONTRACT 1 0+3 pinch", "3 COMMIT CONTRACT 1 0+3 lift",
                "4 RECOGNIZED PUSH 0>1 3+0 swipe", "4 COMMIT PUSH 0>1 3+0 parked",
                // Two fingers on panel 0 and a brief contact at the bottom of panel 1, next to the hinge.
                "9 CANCEL NONE 2+1 edge-start"),
                summary(touches));
    }

    @Test
    public void pinchesAcrossTheHinge() throws IOException {
        List<GeteventReplay.Touch> touches = replay("touch4.getevent", DEFAULTS, 0);
        assertEquals(4, touches.size());
        assertEquals(Arrays.asList(
                // 1 + 2 fingers, the third bounces once, held about 6 s, then pinched in.
                "0 RECOGNIZED CONTRACT 1 1+2 pinch", "0 COMMIT CONTRACT 1 1+2 lift",
                // 2 + 1 fingers spread until they slide off the outer edges.
                "1 RECOGNIZED SPAN 0 2+1 pinch", "1 COMMIT SPAN 0 2+1 parked"),
                summary(touches));
    }

    @Test
    public void recognitionDoesNotDependOnRotation() throws IOException {
        for (String name : new String[] {"touch3.getevent", "touch4.getevent"}) {
            List<String> natural = summary(replay(name, UNIFORM_ZONES, 0));
            assertEquals(name, summary(replay(name, DEFAULTS, 0)), natural);
            for (int rot = 1; rot < 4; rot++) {
                assertEquals(name + " rotation " + rot, natural, summary(replay(name, UNIFORM_ZONES, rot)));
            }
        }
    }

    @Test
    public void recognitionHappensEarlyInTheMotion() throws IOException {
        // Recognised well before the end of the gesture: the thresholds are not the full movement.
        for (String name : new String[] {"touch3.getevent", "touch4.getevent"}) {
            for (GeteventReplay.Touch t : replay(name, DEFAULTS, 0)) {
                if (t.results.size() != 2) continue;
                Result recognized = t.results.get(0);
                Result end = t.results.get(1);
                assertTrue(name, Math.abs(recognized.pinchPx) < 0.5f * Math.abs(end.pinchPx)
                        || Math.abs(recognized.acrossPx) < 0.5f * Math.abs(end.acrossPx));
            }
        }
    }

    private static String abs(double t, String code, long value) {
        return String.format(Locale.ROOT, "[%12.6f] EV_ABS       %-20s %08x", t, code, value & 0xffffffffL);
    }

    private static String syn(double t) {
        return String.format(Locale.ROOT, "[%12.6f] EV_SYN       SYN_REPORT           00000000", t);
    }

    private static long rawX(double displayX) {
        return Math.round(displayX * GeteventReplay.RAW_WIDTH / GeteventReplay.DISPLAY_WIDTH);
    }

    private static long rawY(double displayY) {
        return Math.round(displayY * GeteventReplay.RAW_HEIGHT / GeteventReplay.DISPLAY_HEIGHT);
    }

    @Test
    public void aNewContactInAReusedSlotKeepsTheSlotsCoordinates() throws IOException {
        // Type-B slots keep their axis values: a new tracking id in a reused slot only resends what changed.
        List<String> t = new ArrayList<>();
        // Touch 0: one finger in slot 3 at (900, 950), lifted.
        t.add(abs(1.0, "ABS_MT_SLOT", 3));
        t.add(abs(1.0, "ABS_MT_TRACKING_ID", 100));
        t.add(abs(1.0, "ABS_MT_POSITION_X", rawX(900)));
        t.add(abs(1.0, "ABS_MT_POSITION_Y", rawY(950)));
        t.add(syn(1.0));
        t.add(abs(1.05, "ABS_MT_TRACKING_ID", -1));
        t.add(syn(1.05));
        // Touch 1: three fingers on panel 0 start a pinch-out...
        double[][] f = {{675, 750}, {545, 975}, {805, 975}};
        double[][] dir = {{0, -1}, {-0.866, 0.5}, {0.866, 0.5}};
        for (int i = 0; i < 3; i++) {
            t.add(abs(2.0, "ABS_MT_SLOT", i));
            t.add(abs(2.0, "ABS_MT_TRACKING_ID", 1 + i));
            t.add(abs(2.0, "ABS_MT_POSITION_X", rawX(f[i][0])));
            t.add(abs(2.0, "ABS_MT_POSITION_Y", rawY(f[i][1])));
        }
        t.add(syn(2.0));
        for (int step = 1; step <= 12; step++) {
            double time = 2.0 + 0.01 * step;
            for (int i = 0; i < 3; i++) {
                t.add(abs(time, "ABS_MT_SLOT", i));
                t.add(abs(time, "ABS_MT_POSITION_X", rawX(f[i][0] + 20 * step * dir[i][0])));
                t.add(abs(time, "ABS_MT_POSITION_Y", rawY(f[i][1] + 20 * step * dir[i][1])));
            }
            if (step == 3) {
                // ...and a fourth finger lands in slot 3 at (910, 950): only X differs from the slot's last contact.
                t.add(abs(time, "ABS_MT_SLOT", 3));
                t.add(abs(time, "ABS_MT_TRACKING_ID", 101));
                t.add(abs(time, "ABS_MT_POSITION_X", rawX(910)));
            }
            t.add(syn(time));
        }
        for (int i = 0; i < 4; i++) {
            t.add(abs(2.2, "ABS_MT_SLOT", i));
            t.add(abs(2.2, "ABS_MT_TRACKING_ID", -1));
        }
        t.add(syn(2.2));
        List<GeteventReplay.Touch> touches =
                new GeteventReplay(DEFAULTS, 0).replay(new StringReader(String.join("\n", t)));
        assertEquals(2, touches.size());
        assertEquals(4, touches.get(1).contacts);
        assertEquals(4, touches.get(1).maxDown);
        assertEquals(Arrays.asList("1 CANCEL NONE 3+0 fourth-finger"), summary(touches));
    }

    @Test
    public void parsesInputReaderStyle() throws IOException {
        // Two captures' worth of syntax: optional device prefix, a slot reused with a new tracking id in one frame.
        String trace = String.join("\n",
                "/dev/input/event4: [  10.000000] EV_ABS       ABS_MT_TRACKING_ID   00000001",
                "/dev/input/event4: [  10.000000] EV_ABS       ABS_MT_POSITION_X    00001000",
                "/dev/input/event4: [  10.000000] EV_ABS       ABS_MT_POSITION_Y    00001000",
                "/dev/input/event4: [  10.000000] EV_SYN       SYN_REPORT           00000000",
                "/dev/input/event2: [  10.005000] EV_KEY       KEY_VOLUMEUP         DOWN",
                "[  10.010000] EV_ABS       ABS_MT_SLOT          00000001",
                "[  10.010000] EV_ABS       ABS_MT_TRACKING_ID   00000002",
                "[  10.010000] EV_ABS       ABS_MT_POSITION_X    00002000",
                "[  10.010000] EV_ABS       ABS_MT_POSITION_Y    00001000",
                "[  10.010000] EV_SYN       SYN_REPORT           00000000",
                "[  10.020000] EV_ABS       ABS_MT_SLOT          00000000",
                "[  10.020000] EV_ABS       ABS_MT_TRACKING_ID   00000003",
                "[  10.020000] EV_ABS       ABS_MT_POSITION_X    00001100",
                "[  10.020000] EV_ABS       ABS_MT_POSITION_Y    00001100",
                "[  10.020000] EV_SYN       SYN_REPORT           00000000",
                "[  10.030000] EV_ABS       ABS_MT_TRACKING_ID   ffffffff",
                "[  10.030000] EV_ABS       ABS_MT_SLOT          00000001",
                "[  10.030000] EV_ABS       ABS_MT_TRACKING_ID   ffffffff",
                "[  10.030000] EV_SYN       SYN_REPORT           00000000",
                "[  11.000000] EV_ABS       ABS_MT_TRACKING_ID   00000004",
                "[  11.000000] EV_ABS       ABS_MT_POSITION_X    00001000",
                "[  11.000000] EV_ABS       ABS_MT_POSITION_Y    00001000",
                "[  11.000000] EV_SYN       SYN_REPORT           00000000");
        List<GeteventReplay.Touch> touches = new GeteventReplay(DEFAULTS, 0).replay(new StringReader(trace));
        assertEquals(2, touches.size());
        assertEquals(3, touches.get(0).contacts);
        assertEquals(2, touches.get(0).maxDown);
        assertEquals(10.0, touches.get(0).startSec, 1e-9);
        assertEquals(10.03, touches.get(0).endSec, 1e-9);
        assertEquals(1, touches.get(1).contacts);
    }
}
