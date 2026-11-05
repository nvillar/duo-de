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

import android.app.ActivityOptions;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.Display;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.window.OnBackInvokedDispatcher;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Probe HOME: two large launch areas, one per panel, each listing the probe activities and a few installed apps.
 *
 * A tap launches like a fixed Launcher3 should: an ACTION_MAIN + CATEGORY_LAUNCHER intent with an explicit component
 * (also for the probe's own test activities: the panel routing only considers LAUNCHER intents, see ProbeIntents),
 * FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_RESET_TASK_IF_NEEDED, the on-screen rect of the tapped view as Intent source
 * bounds, and no launch display id and no windowing mode. The "launchDisplayId=0" toggle adds
 * ActivityOptions.setLaunchDisplayId(DEFAULT_DISPLAY) to reproduce what Quickstep sends (which currently bypasses the
 * panel routing).
 */
public class ProbeHomeActivity extends ProbeActivity {
    private static final int MAX_APPS = 8;
    /** Listed first when installed, so both areas show the same predictable apps. */
    private static final List<String> PREFERRED = Arrays.asList(
            "com.android.settings", "com.android.calendar", "com.android.deskclock", "com.android.calculator2",
            "com.android.documentsui", "com.android.contacts", "com.android.messaging", "com.android.camera2");

    private final List<CheckBox> mDisplayIdToggles = new ArrayList<>();
    private boolean mSendDisplayId;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        List<ResolveInfo> apps = launchableApps();
        PanelsLayout root = new PanelsLayout(this);
        root.setBackgroundColor(Color.rgb(24, 24, 32));
        root.addView(buildArea("A", "Area A (left / top)", Color.rgb(40, 70, 120), apps));
        root.addView(buildArea("B", "Area B (right / bottom)", Color.rgb(110, 50, 60), apps));
        installRoot(root);
        // A home is not finished by Back; log the gesture so Back routing can be observed.
        getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                OnBackInvokedDispatcher.PRIORITY_DEFAULT, () -> logState("backInvokedOnHome"));
    }

    private View buildArea(String side, String title, int color, List<ResolveInfo> apps) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(16), dp(16), dp(16), dp(16));

        TextView header = new TextView(this);
        header.setText(title);
        header.setTextSize(24);
        header.setTextColor(Color.WHITE);
        column.addView(header);

        CheckBox toggle = new CheckBox(this);
        toggle.setText("Send launchDisplayId=0 (Quickstep bypass)");
        toggle.setTextColor(Color.WHITE);
        toggle.setChecked(mSendDisplayId);
        toggle.setOnCheckedChangeListener((b, checked) -> setSendDisplayId(checked));
        mDisplayIdToggles.add(toggle);
        column.addView(toggle);
        registerTarget(side + ".toggle", toggle);

        column.addView(target(side + ".probeTest", launchButton(side, "Probe test",
                ProbeIntents.launcher(new ComponentName(this, ProbeTestActivity.class), 0))));
        column.addView(target(side + ".probeTest2", launchButton(side, "Probe test 2 (own task)",
                ProbeIntents.launcher(new ComponentName(this, ProbeOtherTestActivity.class), 0))));
        column.addView(target(side + ".probeTestNew", launchButton(side, "Probe test (new instance)",
                ProbeIntents.launcher(new ComponentName(this, ProbeTestActivity.class),
                        Intent.FLAG_ACTIVITY_NEW_DOCUMENT | Intent.FLAG_ACTIVITY_MULTIPLE_TASK))));
        column.addView(target(side + ".probeLocked", launchButton(side, "Probe locked (showWhenLocked)",
                ProbeIntents.launcher(new ComponentName(this, ProbeLockedTestActivity.class), 0))));

        PackageManager pm = getPackageManager();
        for (ResolveInfo ri : apps) {
            Intent intent = ProbeIntents.launcher(
                    new ComponentName(ri.activityInfo.packageName, ri.activityInfo.name), 0);
            column.addView(launchButton(side, String.valueOf(ri.loadLabel(pm)), intent));
        }

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(color);
        scroll.addView(column);
        return scroll;
    }

    private View target(String name, View view) {
        registerTarget(name, view);
        return view;
    }

    private Button launchButton(String side, String label, Intent intent) {
        Button b = new Button(this);
        b.setText(label);
        b.setTextSize(18);
        b.setAllCaps(false);
        b.setGravity(Gravity.CENTER_VERTICAL | Gravity.START);
        b.setMinHeight(dp(64));
        b.setOnClickListener(v -> launch(v, side, label, new Intent(intent)));
        return b;
    }

    private void setSendDisplayId(boolean send) {
        if (mSendDisplayId == send) return;
        mSendDisplayId = send;
        for (CheckBox c : mDisplayIdToggles) {
            if (c.isChecked() != send) c.setChecked(send);
        }
        log("toggle", "sendLaunchDisplayId=" + send);
    }

    private void launch(View v, String side, String label, Intent intent) {
        if (!ProbeIntents.isLauncherIntent(intent)) {
            // A non-LAUNCHER intent bypasses the panel routing and would make the gate result meaningless.
            log("launchFailed", "label=" + label.replace(' ', '_') + " reason=notLauncherIntent intent=" + intent);
            return;
        }
        int[] loc = new int[2];
        v.getLocationOnScreen(loc);
        Rect source = new Rect(loc[0], loc[1], loc[0] + v.getWidth(), loc[1] + v.getHeight());
        intent.setSourceBounds(source);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        ActivityOptions options = ActivityOptions.makeBasic();
        if (mSendDisplayId) options.setLaunchDisplayId(Display.DEFAULT_DISPLAY);
        log("launch", "side=" + side + " label=" + label.replace(' ', '_')
                + " component=" + intent.getComponent().flattenToShortString()
                + " action=" + intent.getAction()
                + " categories=" + intent.getCategories()
                + " flags=0x" + Integer.toHexString(intent.getFlags())
                + " sourceBounds=" + source.toShortString()
                + " launchDisplayId=" + (mSendDisplayId ? "0" : "unset")
                + " windowingMode=unset");
        try {
            startActivity(intent, options.toBundle());
        } catch (ActivityNotFoundException | SecurityException e) {
            log("launchFailed", "error=" + e);
        }
    }

    private List<ResolveInfo> launchableApps() {
        Intent query = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        PackageManager pm = getPackageManager();
        List<ResolveInfo> all = new ArrayList<>(pm.queryIntentActivities(query, 0));
        all.removeIf(ri -> getPackageName().equals(ri.activityInfo.packageName));
        all.sort((a, b) -> {
            int pa = rank(a), pb = rank(b);
            if (pa != pb) return Integer.compare(pa, pb);
            return String.valueOf(a.loadLabel(pm)).compareToIgnoreCase(String.valueOf(b.loadLabel(pm)));
        });
        return all.size() > MAX_APPS ? new ArrayList<>(all.subList(0, MAX_APPS)) : all;
    }

    private static int rank(ResolveInfo ri) {
        int i = PREFERRED.indexOf(ri.activityInfo.packageName);
        return i < 0 ? PREFERRED.size() : i;
    }
}
