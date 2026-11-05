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

package org.duode.gate;

import android.app.ActivityManager;
import android.app.ActivityTaskManager;
import android.app.IActivityController;
import android.content.Intent;

/**
 * Device test helper for a rejected predictive Back commit (ledger F22, frameworks_base 0024). Runs as the shell user
 * through app_process, which holds SET_ACTIVITY_WATCHER and MANAGE_ACTIVITY_TASKS:
 *
 * <pre>
 * front &lt;taskId&gt;              move a task to the top of its task display area (setFocusedTask)
 * veto &lt;package&gt; [seconds]   install an activity controller that refuses activityResuming for the package, then
 *                             remove it (default 20 s)
 * </pre>
 *
 * Task.canMoveTaskToBack asks the controller whether the activity that would resume next in the task display area may
 * resume; refusing it rejects the move, so Back at the root of a home-launched task does nothing. `am monitor` cannot
 * do this: its controller always allows activityResuming.
 */
public final class BackVeto {
    public static void main(String[] args) throws Exception {
        if (args.length >= 2 && "front".equals(args[0])) {
            ActivityTaskManager.getService().setFocusedTask(Integer.parseInt(args[1]));
            System.out.println("front task " + args[1]);
            return;
        }
        if (args.length >= 2 && "veto".equals(args[0])) {
            veto(args[1], args.length >= 3 ? Long.parseLong(args[2]) : 20);
            return;
        }
        System.err.println("usage: front <taskId> | veto <package> [seconds]");
        System.exit(2);
    }

    private static void veto(String pkg, long seconds) throws Exception {
        final IActivityController controller = new IActivityController.Stub() {
            @Override
            public boolean activityStarting(Intent intent, String p) {
                return true;
            }

            @Override
            public boolean activityResuming(String p) {
                final boolean refuse = pkg.equals(p);
                System.out.println("activityResuming " + p + (refuse ? " -> refused" : " -> allowed"));
                System.out.flush();
                return !refuse;
            }

            @Override
            public boolean appCrashed(String processName, int pid, String shortMsg, String longMsg,
                    long timeMillis, String stackTrace) {
                return true;
            }

            @Override
            public int appEarlyNotResponding(String processName, int pid, String annotation) {
                return 0;
            }

            @Override
            public int appNotResponding(String processName, int pid, String processStats) {
                return 0;
            }

            @Override
            public int systemNotResponding(String msg) {
                return -1;
            }
        };
        ActivityManager.getService().setActivityController(controller, false /* imAMonkey */);
        System.out.println("vetoing activityResuming for " + pkg + " for " + seconds + " s");
        System.out.flush();
        try {
            Thread.sleep(seconds * 1000);
        } finally {
            ActivityManager.getService().setActivityController(null, false /* imAMonkey */);
            System.out.println("controller removed");
        }
    }
}
