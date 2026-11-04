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

import android.graphics.Color;

/**
 * Keyguard gate: the test activity declared android:showWhenLocked="true" in its own task. With it on one panel and
 * ordinary content on the other, locking must not leave the other panel's content visible or touchable.
 */
public class ProbeLockedTestActivity extends ProbeTestActivity {
    @Override
    protected String title() {
        return "Duo probe LOCKED (showWhenLocked)";
    }

    @Override
    protected int background() {
        return Color.rgb(120, 80, 20);
    }
}
