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

import android.content.ComponentName;
import android.content.Intent;

/**
 * The one way the probe home builds launch intents. The panel routing (frameworks_base 0008, panelForLaunch) only
 * routes a launch from home that is a LAUNCHER intent with source bounds, so every probe launch, including the probe's
 * own test activities, is ACTION_MAIN + CATEGORY_LAUNCHER with an explicit component, exactly like an app icon tap.
 * The probe activities need no matching intent filter for this (explicit same-app intent; receiver-side intent
 * matching is opt-in on Android 16), so they do not show up in other launchers.
 */
final class ProbeIntents {
    private ProbeIntents() {
    }

    static Intent launcher(ComponentName component, int extraFlags) {
        return new Intent(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .setComponent(component)
                .addFlags(extraFlags);
    }

    /** True for intents panelForLaunch can route: ACTION_MAIN, CATEGORY_LAUNCHER and an explicit component. */
    static boolean isLauncherIntent(Intent intent) {
        return intent != null && Intent.ACTION_MAIN.equals(intent.getAction())
                && intent.hasCategory(Intent.CATEGORY_LAUNCHER) && intent.getComponent() != null;
    }
}
