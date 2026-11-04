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

package org.duode.probe;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Context;
import android.content.res.Configuration;
import android.util.Log;
import android.view.Display;
import android.view.WindowManager;
import android.view.WindowMetrics;

import java.util.List;

/**
 * Logcat output of the probe. Every line has the tag {@link #TAG} and starts with {@code event=<name> act=<class@id>
 * task=<id>} followed by space-separated key=value pairs, so captures can be grepped and diffed.
 */
final class ProbeLog {
    static final String TAG = "DuoProbe";

    private ProbeLog() {
    }

    static void log(Activity a, String event, String details) {
        StringBuilder sb = new StringBuilder("event=").append(event)
                .append(" act=").append(a.getClass().getSimpleName()).append('@')
                .append(Integer.toHexString(System.identityHashCode(a)))
                .append(" task=").append(a.getTaskId());
        if (details != null && !details.isEmpty()) sb.append(' ').append(details);
        Log.i(TAG, sb.toString());
    }

    /** Display, rotation, window bounds and the configuration summary of the activity. */
    static String window(Activity a) {
        StringBuilder sb = new StringBuilder();
        try {
            Display d = a.getDisplay();
            sb.append("display=").append(d.getDisplayId()).append(" rotation=").append(d.getRotation());
        } catch (RuntimeException e) {
            sb.append("display=? ");
        }
        try {
            WindowManager wm = a.getWindowManager();
            WindowMetrics cur = wm.getCurrentWindowMetrics();
            WindowMetrics max = wm.getMaximumWindowMetrics();
            sb.append(" bounds=").append(cur.getBounds().toShortString())
                    .append(" maxBounds=").append(max.getBounds().toShortString());
        } catch (RuntimeException e) {
            sb.append(" bounds=?");
        }
        Configuration c = a.getResources().getConfiguration();
        sb.append(" multiWindow=").append(a.isInMultiWindowMode())
                .append(" orientation=").append(orientationName(c.orientation))
                .append(" sizeDp=").append(c.screenWidthDp).append('x').append(c.screenHeightDp)
                .append(" swDp=").append(c.smallestScreenWidthDp)
                .append(" dpi=").append(c.densityDpi)
                .append(" requestedOrientation=").append(a.getRequestedOrientation());
        KeyguardManager km = a.getSystemService(KeyguardManager.class);
        if (km != null) sb.append(" keyguardLocked=").append(km.isKeyguardLocked());
        return sb.toString();
    }

    /** The full configuration; its toString includes the window configuration (bounds, windowing mode). */
    static void config(Activity a, String event) {
        log(a, event, "config={" + a.getResources().getConfiguration() + "}");
    }

    /**
     * The Surface Duo hinge as the platform DisplayMask (com.microsoft.device boot jar) reports it to apps, for the
     * given rotation, read reflectively so the probe has no compile-time dependency on it.
     */
    static String displayMask(Context context, int rotation) {
        try {
            Class<?> cls = Class.forName("com.microsoft.device.display.DisplayMask");
            Object mask = cls.getMethod("fromResourcesRect", Context.class).invoke(null, context);
            Object rects = cls.getMethod("getBoundingRectsForRotation", int.class).invoke(mask, rotation);
            return rects instanceof List ? rects.toString().replace(' ', '_') : String.valueOf(rects);
        } catch (ClassNotFoundException e) {
            return "absent";
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            return "error:" + e.getClass().getSimpleName();
        }
    }

    static String orientationName(int o) {
        switch (o) {
            case Configuration.ORIENTATION_PORTRAIT: return "portrait";
            case Configuration.ORIENTATION_LANDSCAPE: return "landscape";
            default: return "undefined";
        }
    }
}
