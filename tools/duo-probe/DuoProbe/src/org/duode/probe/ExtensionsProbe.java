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

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

/**
 * Logs the WindowLayoutInfo (display features: fold/hinge bounds, type, state) that the platform's
 * androidx.window.extensions library reports for an activity, the same source Jetpack WindowManager reads, without
 * linking Jetpack. Everything is reflective: when the library or the method is missing the probe logs that and goes on.
 */
final class ExtensionsProbe {
    private final Activity mActivity;
    private Object mComponent;
    private Method mRemove;
    private Object mListener;

    ExtensionsProbe(Activity activity) {
        mActivity = activity;
    }

    void start() {
        if (mListener != null) return;
        try {
            Class<?> provider = Class.forName("androidx.window.extensions.WindowExtensionsProvider");
            Class<?> extensionsType = Class.forName("androidx.window.extensions.WindowExtensions");
            Object extensions = provider.getMethod("getWindowExtensions").invoke(null);
            Object level = extensionsType.getMethod("getVendorApiLevel").invoke(extensions);
            Object component = extensionsType.getMethod("getWindowLayoutComponent").invoke(extensions);
            if (component == null) {
                ProbeLog.log(mActivity, "windowExtensions", "vendorApiLevel=" + level + " layoutComponent=null");
                return;
            }
            Class<?> componentType = Class.forName("androidx.window.extensions.layout.WindowLayoutComponent");
            Method add = null;
            for (Method m : componentType.getMethods()) {
                Class<?>[] p = m.getParameterTypes();
                if (m.getName().equals("addWindowLayoutInfoListener") && p.length == 2
                        && p[0] == Activity.class && p[1].isInterface()) {
                    add = m;
                    break;
                }
            }
            if (add == null) {
                ProbeLog.log(mActivity, "windowExtensions", "vendorApiLevel=" + level + " addListener=missing");
                return;
            }
            Class<?> consumerType = add.getParameterTypes()[1];
            Method remove = componentType.getMethod("removeWindowLayoutInfoListener", consumerType);
            InvocationHandler handler = (proxy, method, args) -> {
                switch (method.getName()) {
                    case "accept":
                        ProbeLog.log(mActivity, "windowLayoutInfo",
                                "info=" + String.valueOf(args == null ? null : args[0]).replace(' ', '_'));
                        return null;
                    case "hashCode":
                        return System.identityHashCode(proxy);
                    case "equals":
                        return args != null && proxy == args[0];
                    case "toString":
                        return "DuoProbeWindowLayoutListener";
                    default:
                        return null;
                }
            };
            Object listener = Proxy.newProxyInstance(ExtensionsProbe.class.getClassLoader(),
                    new Class<?>[] {consumerType}, handler);
            add.invoke(component, mActivity, listener);
            mComponent = component;
            mRemove = remove;
            mListener = listener;
            ProbeLog.log(mActivity, "windowExtensions", "vendorApiLevel=" + level + " listening=true");
        } catch (ClassNotFoundException e) {
            ProbeLog.log(mActivity, "windowExtensions", "absent");
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            ProbeLog.log(mActivity, "windowExtensions", "error=" + e);
        }
    }

    void stop() {
        if (mListener == null) return;
        try {
            mRemove.invoke(mComponent, mListener);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            ProbeLog.log(mActivity, "windowExtensions", "removeError=" + e);
        }
        mComponent = null;
        mRemove = null;
        mListener = null;
    }
}
