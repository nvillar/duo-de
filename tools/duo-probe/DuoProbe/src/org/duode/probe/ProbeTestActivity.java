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

import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * An ordinary app window for the gates: two editors (IME gate, switching editor focus), an orientation request button,
 * IME show/hide, a launch-adjacent button and lifecycle/configuration/window logging (see {@link ProbeActivity}).
 */
public class ProbeTestActivity extends ProbeActivity {
    private static final int[] ORIENTATIONS = {
        ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED,
        ActivityInfo.SCREEN_ORIENTATION_PORTRAIT,
        ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE,
    };
    private static final String[] ORIENTATION_NAMES = {"unspecified", "portrait", "landscape"};

    private TextView mInfo;
    private Button mOrientation;

    /** Title shown at the top; the showWhenLocked variant overrides it. */
    protected String title() {
        return "Duo probe test";
    }

    protected int background() {
        return Color.rgb(30, 60, 40);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(16), dp(16), dp(16), dp(16));

        TextView header = new TextView(this);
        header.setText(title());
        header.setTextSize(24);
        header.setTextColor(Color.WHITE);
        column.addView(header);

        mInfo = new TextView(this);
        mInfo.setTextColor(Color.WHITE);
        mInfo.setTextIsSelectable(true);
        column.addView(mInfo);

        EditText single = new EditText(this);
        single.setHint("Editor 1: tap to type (IME gate)");
        single.setInputType(InputType.TYPE_CLASS_TEXT);
        single.setOnFocusChangeListener((v, f) -> log("editorFocus", "editor=1 focus=" + f));
        column.addView(single);

        EditText multi = new EditText(this);
        multi.setHint("Editor 2: multi-line");
        multi.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        multi.setMinLines(3);
        multi.setOnFocusChangeListener((v, f) -> log("editorFocus", "editor=2 focus=" + f));
        column.addView(multi);

        column.addView(button("Show IME", v -> {
            single.requestFocus();
            getWindow().getInsetsController().show(WindowInsets.Type.ime());
            log("imeRequest", "show");
        }));
        column.addView(button("Hide IME", v -> {
            getWindow().getInsetsController().hide(WindowInsets.Type.ime());
            log("imeRequest", "hide");
        }));
        mOrientation = button("", v -> cycleOrientation());
        updateOrientationLabel();
        column.addView(mOrientation);
        column.addView(button("Launch another test task adjacent", v -> {
            Intent i = new Intent(this, ProbeTestActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_MULTIPLE_TASK | Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT);
            log("launch", "component=" + i.getComponent().flattenToShortString()
                    + " flags=0x" + Integer.toHexString(i.getFlags()));
            startActivity(i);
        }));
        column.addView(button("Log state now", v -> {
            logState("manual");
            ProbeLog.config(this, "configManual");
            refreshInfo();
        }));
        column.addView(button("Finish", v -> finish()));

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(background());
        scroll.addView(column);
        installRoot(scroll);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshInfo();
    }

    @Override
    public void onConfigurationChanged(android.content.res.Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        refreshInfo();
    }

    private Button button(String label, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setMinHeight(dp(56));
        b.setOnClickListener(l);
        return b;
    }

    private void cycleOrientation() {
        int current = getRequestedOrientation();
        int index = 0;
        for (int i = 0; i < ORIENTATIONS.length; i++) {
            if (ORIENTATIONS[i] == current) index = (i + 1) % ORIENTATIONS.length;
        }
        log("requestOrientation", "requested=" + ORIENTATION_NAMES[index]);
        setRequestedOrientation(ORIENTATIONS[index]);
        updateOrientationLabel();
    }

    private void updateOrientationLabel() {
        int current = getRequestedOrientation();
        String name = String.valueOf(current);
        for (int i = 0; i < ORIENTATIONS.length; i++) {
            if (ORIENTATIONS[i] == current) name = ORIENTATION_NAMES[i];
        }
        mOrientation.setText("Requested orientation: " + name + " (tap to cycle)");
    }

    private void refreshInfo() {
        if (mInfo != null) mInfo.setText(ProbeLog.window(this).replace(' ', '\n'));
    }
}
