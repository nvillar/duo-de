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

import com.android.internal.policy.DuoPanelGeometry;
import com.android.systemui.duo.gesture.DuoThreeFingerClassifier;
import com.android.systemui.duo.gesture.DuoThreeFingerClassifier.Config;
import com.android.systemui.duo.gesture.DuoThreeFingerClassifier.DisplayState;
import com.android.systemui.duo.gesture.DuoThreeFingerClassifier.Result;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.PrintStream;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Replays a raw multi-touch capture ({@code adb shell getevent -lt /dev/input/eventN}) through
 * {@link DuoThreeFingerClassifier}, the way Android's InputReader would turn it into MotionEvents: raw coordinates
 * scaled to the display, then rotated; pointer ids are the lowest free ids; per frame (SYN_REPORT) the lifted
 * pointers go up first, then one MOVE if a remaining pointer moved, then the new pointers go down.
 */
public final class GeteventReplay {
    /** Surface Duo 1 {@code surface_touchscreen}: raw 0-17709 x 0-11411 onto the 2784 x 1800 spanned display. */
    public static final int RAW_WIDTH = 17710;
    public static final int RAW_HEIGHT = 11412;
    public static final int DISPLAY_WIDTH = 2784;
    public static final int DISPLAY_HEIGHT = 1800;

    private static final Pattern LINE = Pattern.compile(
            "^(?:(\\S+):\\s*)?\\[\\s*([0-9.]+)\\]\\s+(?:(\\S+):\\s+)?(EV_\\w+)\\s+(\\w+)\\s+([0-9a-fA-F]+)\\s*$");

    /** One touch, from the first contact down to the last up, for a summary of the capture. */
    public static final class Touch {
        public double startSec;
        public double endSec;
        public int contacts;
        public int maxDown;
        public final List<Result> results = new ArrayList<>();
    }

    private final Config mConfig;
    private final int mRotation;
    private final DuoPanelGeometry.Spec mSpec;
    private final int mRawWidth;
    private final int mRawHeight;
    private final int mWidth;
    private final int mHeight;
    private final String mDevice;

    public GeteventReplay(Config config, int rotation) {
        this(config, rotation, DuoPanelGeometry.DUO, RAW_WIDTH, RAW_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT, null);
    }

    /** {@code width x height} is the display in its natural (rotation 0) orientation; device filters lines. */
    public GeteventReplay(Config config, int rotation, DuoPanelGeometry.Spec spec, int rawWidth, int rawHeight,
            int width, int height, String device) {
        mConfig = config;
        mRotation = rotation & 3;
        mSpec = spec;
        mRawWidth = rawWidth;
        mRawHeight = rawHeight;
        mWidth = width;
        mHeight = height;
        mDevice = device;
    }

    public List<Touch> replay(Reader input) throws IOException {
        final List<Touch> touches = new ArrayList<>();
        final Touch[] current = new Touch[1];
        boolean portrait = (mRotation & 1) == 1;
        final DisplayState display = DisplayState.of(mSpec, portrait ? mHeight : mWidth,
                portrait ? mWidth : mHeight, mRotation, null);
        DuoThreeFingerClassifier classifier = new DuoThreeFingerClassifier(mConfig, () -> display,
                r -> current[0].results.add(r));

        Map<Integer, long[]> slots = new HashMap<>();   // slot -> {tracking id, raw x, raw y}; -1 = unknown
        TreeMap<Integer, float[]> pointers = new TreeMap<>();  // pointer id -> display x, y
        Map<Long, Integer> pointerOf = new HashMap<>();  // tracking id -> pointer id
        int slot = 0;
        long lastMs = 0;
        BufferedReader in = new BufferedReader(input);
        String line;
        while ((line = in.readLine()) != null) {
            Matcher m = LINE.matcher(line);
            if (!m.matches()) continue;
            String device = m.group(1) != null ? m.group(1) : m.group(3);
            if (mDevice != null && device != null && !mDevice.equals(device)) continue;
            double sec = Double.parseDouble(m.group(2));
            String type = m.group(4);
            String code = m.group(5);
            long value = Long.parseLong(m.group(6), 16);
            if (type.equals("EV_ABS")) {
                long signed = (int) value;
                switch (code) {
                    case "ABS_MT_SLOT":
                        slot = (int) value;
                        break;
                    case "ABS_MT_TRACKING_ID":
                        if (signed < 0) {
                            slots.remove(slot);
                        } else {
                            slots.put(slot, new long[] {signed, -1, -1});
                        }
                        break;
                    case "ABS_MT_POSITION_X":
                        if (slots.containsKey(slot)) slots.get(slot)[1] = value;
                        break;
                    case "ABS_MT_POSITION_Y":
                        if (slots.containsKey(slot)) slots.get(slot)[2] = value;
                        break;
                    default:
                        break;
                }
            } else if (type.equals("EV_SYN") && code.equals("SYN_REPORT")) {
                long timeMs = Math.round(sec * 1000);
                lastMs = timeMs;
                // Contacts of this frame, by tracking id.
                Map<Long, float[]> now = new HashMap<>();
                for (long[] s : slots.values()) {
                    if (s[1] >= 0 && s[2] >= 0) now.put(s[0], toDisplay(s[1], s[2]));
                }
                // Lifted pointers first, with the latest positions of the others.
                List<Integer> ups = new ArrayList<>();
                for (Map.Entry<Long, Integer> e : pointerOf.entrySet()) {
                    if (!now.containsKey(e.getKey())) ups.add(e.getValue());
                }
                boolean moved = false;
                for (Map.Entry<Long, float[]> e : now.entrySet()) {
                    Integer id = pointerOf.get(e.getKey());
                    if (id == null) continue;
                    float[] p = pointers.get(id);
                    if (p[0] != e.getValue()[0] || p[1] != e.getValue()[1]) {
                        p[0] = e.getValue()[0];
                        p[1] = e.getValue()[1];
                        moved = true;
                    }
                }
                ups.sort(null);
                for (int id : ups) {
                    int action = pointers.size() == 1 ? DuoThreeFingerClassifier.ACTION_UP
                            : DuoThreeFingerClassifier.ACTION_POINTER_UP;
                    send(classifier, action, pointers.headMap(id).size(), timeMs, pointers);
                    pointers.remove(id);
                    pointerOf.values().remove(id);
                    if (pointers.isEmpty()) {
                        current[0].endSec = sec;
                        current[0] = null;
                    }
                }
                if (moved && !pointers.isEmpty()) {
                    send(classifier, DuoThreeFingerClassifier.ACTION_MOVE, 0, timeMs, pointers);
                }
                List<Long> downs = new ArrayList<>();
                for (long tid : now.keySet()) {
                    if (!pointerOf.containsKey(tid)) downs.add(tid);
                }
                downs.sort(null);
                for (long tid : downs) {
                    int id = 0;
                    while (pointers.containsKey(id)) id++;
                    pointerOf.put(tid, id);
                    pointers.put(id, now.get(tid).clone());
                    if (current[0] == null) {
                        current[0] = new Touch();
                        current[0].startSec = sec;
                        touches.add(current[0]);
                    }
                    current[0].contacts++;
                    current[0].maxDown = Math.max(current[0].maxDown, pointers.size());
                    int action = pointers.size() == 1 ? DuoThreeFingerClassifier.ACTION_DOWN
                            : DuoThreeFingerClassifier.ACTION_POINTER_DOWN;
                    send(classifier, action, pointers.headMap(id).size(), timeMs, pointers);
                }
            }
        }
        if (current[0] != null && !pointers.isEmpty()) {
            // The capture ended with fingers down: Android would not see the end either; cancel to flush.
            send(classifier, DuoThreeFingerClassifier.ACTION_CANCEL, 0, lastMs, pointers);
        }
        return touches;
    }

    private float[] toDisplay(long rawX, long rawY) {
        float x = rawX * (float) mWidth / mRawWidth;
        float y = rawY * (float) mHeight / mRawHeight;
        switch (mRotation) {
            case 1: return new float[] {y, mWidth - x};
            case 2: return new float[] {mWidth - x, mHeight - y};
            case 3: return new float[] {mHeight - y, x};
            default: return new float[] {x, y};
        }
    }

    private static void send(DuoThreeFingerClassifier c, int action, int index, long timeMs,
            TreeMap<Integer, float[]> pointers) {
        int n = pointers.size();
        int[] ids = new int[n];
        float[] xs = new float[n];
        float[] ys = new float[n];
        int i = 0;
        for (Map.Entry<Integer, float[]> e : pointers.entrySet()) {
            ids[i] = e.getKey();
            xs[i] = e.getValue()[0];
            ys[i] = e.getValue()[1];
            i++;
        }
        c.onTouchEvent(action, index, timeMs, n, ids, xs, ys);
    }

    private static void usage(PrintStream out) {
        out.println("usage: replay [--config k=v,...] [--rotation 0-3] [--device /dev/input/eventN] [--all]"
                + " capture.getevent...");
        out.println("  Replays 'adb shell getevent -lt' multi-touch captures through the three-finger classifier");
        out.println("  and prints one line per result (the on-device DuoGesture log format). --all also lists");
        out.println("  touches that reported nothing. Config keys: " + new Config());
    }

    public static void main(String[] args) throws IOException {
        Config config = Config.forDensity(2.5f);
        int rotation = 0;
        String device = null;
        boolean all = false;
        List<String> files = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--config": config = Config.parse(args[++i], config); break;
                case "--rotation": rotation = Integer.parseInt(args[++i]); break;
                case "--device": device = args[++i]; break;
                case "--all": all = true; break;
                case "-h":
                case "--help": usage(System.out); return;
                default:
                    if (args[i].startsWith("-")) {
                        usage(System.err);
                        System.exit(2);
                    }
                    files.add(args[i]);
            }
        }
        if (files.isEmpty()) {
            usage(System.err);
            System.exit(2);
        }
        System.out.println("# config " + config + " rotation=" + rotation);
        for (String file : files) {
            GeteventReplay replay = new GeteventReplay(config, rotation, DuoPanelGeometry.DUO, RAW_WIDTH,
                    RAW_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT, device);
            List<Touch> touches;
            try (Reader r = Files.newBufferedReader(Paths.get(file), StandardCharsets.UTF_8)) {
                touches = replay.replay(r);
            }
            System.out.println("# " + file + ": " + touches.size() + " touches");
            for (int i = 0; i < touches.size(); i++) {
                Touch t = touches.get(i);
                String head = String.format(Locale.ROOT, "touch=%d t=%.2f contacts=%d maxDown=%d", i,
                        t.startSec, t.contacts, t.maxDown);
                if (t.results.isEmpty() && all) System.out.println(head + " (nothing)");
                for (Result res : t.results) System.out.println(head + " " + res.toLogString());
            }
        }
    }
}
