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
