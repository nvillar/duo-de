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
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Insets;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Base of the probe activities: logs the lifecycle, focus, configuration, window bounds, insets and display features
 * with {@link ProbeLog}, every touch the window receives ({@code touch}) and the on-screen rectangles of its named
 * controls ({@code targets}), so a scripted gate can tap them and prove which task received the input.
 */
public abstract class ProbeActivity extends Activity {
    private ExtensionsProbe mExtensions;
    private String mLastLayout;
    private String mLastInsets;
    private String mLastTargets;
    private final Map<String, View> mTargets = new LinkedHashMap<>();

    protected void log(String event, String details) {
        ProbeLog.log(this, event, details);
    }

    protected void logState(String event) {
        log(event, ProbeLog.window(this));
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        mExtensions = new ExtensionsProbe(this);
        Intent i = getIntent();
        log("onCreate", "relaunched=" + (savedInstanceState != null)
                + " flags=0x" + Integer.toHexString(i == null ? 0 : i.getFlags())
                + " sourceBounds=" + (i == null || i.getSourceBounds() == null
                        ? "null" : i.getSourceBounds().toShortString())
                + " " + ProbeLog.window(this));
        ProbeLog.config(this, "configOnCreate");
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        log("onNewIntent", "flags=0x" + Integer.toHexString(intent.getFlags())
                + " sourceBounds=" + (intent.getSourceBounds() == null
                        ? "null" : intent.getSourceBounds().toShortString()));
    }

    @Override
    protected void onStart() {
        super.onStart();
        logState("onStart");
        mExtensions.start();
        int rotation = 0;
        try {
            rotation = getDisplay().getRotation();
        } catch (RuntimeException ignored) {
        }
        log("displayMask", "rotation=" + rotation + " rects=" + ProbeLog.displayMask(this, rotation));
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        logState("onRestart");
    }

    @Override
    protected void onResume() {
        super.onResume();
        logState("onResume");
    }

    @Override
    public void onTopResumedActivityChanged(boolean isTopResumedActivity) {
        super.onTopResumedActivityChanged(isTopResumedActivity);
        log("onTopResumedActivityChanged", "top=" + isTopResumedActivity);
    }

    @Override
    protected void onPause() {
        logState("onPause");
        super.onPause();
    }

    @Override
    protected void onStop() {
        logState("onStop");
        mExtensions.stop();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        log("onDestroy", "finishing=" + isFinishing() + " changingConfigurations=" + isChangingConfigurations());
        super.onDestroy();
    }

    @Override
    protected void onUserLeaveHint() {
        super.onUserLeaveHint();
        log("onUserLeaveHint", "");
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        log("onWindowFocusChanged", "focus=" + hasFocus);
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        logState("onConfigurationChanged");
        ProbeLog.config(this, "config");
    }

    /**
     * Real input receipt for the gate collector: logs where each touch landed (window and raw display coordinates).
     * Only down/up/cancel, never moves.
     */
    @Override
    public boolean dispatchTouchEvent(MotionEvent ev) {
        int action = ev.getActionMasked();
        String name = action == MotionEvent.ACTION_DOWN ? "down"
                : action == MotionEvent.ACTION_UP ? "up"
                : action == MotionEvent.ACTION_CANCEL ? "cancel" : null;
        if (name != null) {
            log("touch", "action=" + name + " x=" + Math.round(ev.getX()) + " y=" + Math.round(ev.getY())
                    + " rawX=" + Math.round(ev.getRawX()) + " rawY=" + Math.round(ev.getRawY())
                    + " pointers=" + ev.getPointerCount());
        }
        return super.dispatchTouchEvent(ev);
    }

    /** Names a control whose on-screen rectangle is logged in the {@code targets} event (no spaces in the name). */
    protected void registerTarget(String name, View view) {
        mTargets.put(name, view);
    }

    private void logTargets() {
        if (mTargets.isEmpty()) return;
        StringBuilder sb = new StringBuilder();
        int[] loc = new int[2];
        for (Map.Entry<String, View> e : mTargets.entrySet()) {
            View v = e.getValue();
            if (!v.isAttachedToWindow() || v.getWidth() == 0 || v.getHeight() == 0) continue;
            v.getLocationOnScreen(loc);
            Rect r = new Rect(loc[0], loc[1], loc[0] + v.getWidth(), loc[1] + v.getHeight());
            if (sb.length() > 0) sb.append(' ');
            sb.append(e.getKey()).append('=').append(r.toShortString());
        }
        String s = sb.toString();
        if (!s.isEmpty() && !s.equals(mLastTargets)) {
            mLastTargets = s;
            log("targets", s);
        }
    }

    @Override
    public void onMultiWindowModeChanged(boolean isInMultiWindowMode, Configuration newConfig) {
        super.onMultiWindowModeChanged(isInMultiWindowMode, newConfig);
        log("onMultiWindowModeChanged", "multiWindow=" + isInMultiWindowMode);
    }

    /**
     * Content root setup shared by the activities: draws edge to edge, pads the root by the system bars, cutout and
     * IME, and logs the insets and the on-screen position of the root whenever they change.
     */
    protected void installRoot(View root) {
        getWindow().setDecorFitsSystemWindows(false);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            int types = WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout();
            Insets bars = insets.getInsets(types);
            Insets ime = insets.getInsets(WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            String s = "imeVisible=" + insets.isVisible(WindowInsets.Type.ime())
                    + " ime=" + compact(ime) + " bars=" + compact(bars)
                    + " cutout=" + String.valueOf(insets.getDisplayCutout()).replace(' ', '_');
            if (!s.equals(mLastInsets)) {
                mLastInsets = s;
                log("insets", s);
            }
            return WindowInsets.CONSUMED;
        });
        root.getViewTreeObserver().addOnGlobalLayoutListener(() -> {
            int[] loc = new int[2];
            root.getLocationOnScreen(loc);
            Rect onScreen = new Rect(loc[0], loc[1], loc[0] + root.getWidth(), loc[1] + root.getHeight());
            String s = "rootOnScreen=" + onScreen.toShortString();
            if (!s.equals(mLastLayout)) {
                mLastLayout = s;
                log("layout", s + " " + ProbeLog.window(this));
            }
            logTargets();
        });
        setContentView(root);
    }

    private static String compact(Insets i) {
        return "[" + i.left + "," + i.top + "," + i.right + "," + i.bottom + "]";
    }

    protected int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }
}
