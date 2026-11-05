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
 * The same test window in a second reusable task (its own task affinity). With {@link ProbeTestActivity} on one panel
 * and this one on the other, each side can be minimized and relaunched (reused) independently, which the "new
 * instance" button cannot do (it always creates another task).
 */
public class ProbeOtherTestActivity extends ProbeTestActivity {
    @Override
    protected String title() {
        return "Duo probe test 2 (own task)";
    }

    @Override
    protected int background() {
        return Color.rgb(40, 50, 90);
    }
}
