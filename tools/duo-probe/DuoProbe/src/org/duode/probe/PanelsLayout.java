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

import android.content.Context;
import android.graphics.Point;
import android.graphics.Rect;
import android.view.Display;
import android.view.View;
import android.view.ViewGroup;

/**
 * Lays out exactly two children, area A and area B, one per physical panel: side by side with the hinge band between
 * them when the panels are side by side, stacked when the display is rotated 90/270. The hinge comes from the known
 * spanned/gapless display sizes (same table as DuoPanelGeometry: Duo 1350/84/1800, Duo 2 1344/66/1892). On any other
 * size (a single panel, another device) the areas split the view in half with a small gap.
 */
final class PanelsLayout extends ViewGroup {
    private static final int[][] SPECS = {{1350, 84, 1800}, {1344, 66, 1892}};

    private final Rect mA = new Rect();
    private final Rect mB = new Rect();
    private boolean mStacked;

    PanelsLayout(Context context) {
        super(context);
    }

    /** True when the areas are stacked (A on top, B below) rather than side by side. */
    boolean isStacked() {
        return mStacked;
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int w = MeasureSpec.getSize(widthMeasureSpec);
        int h = MeasureSpec.getSize(heightMeasureSpec);
        setMeasuredDimension(w, h);
        computeAreas(w, h);
        measureArea(0, mA);
        measureArea(1, mB);
    }

    @Override
    protected void onLayout(boolean changed, int l, int t, int r, int b) {
        computeAreas(r - l, b - t);
        layoutArea(0, mA);
        layoutArea(1, mB);
    }

    private void measureArea(int index, Rect area) {
        View child = getChildAt(index);
        if (child == null) return;
        child.measure(MeasureSpec.makeMeasureSpec(Math.max(0, area.width()), MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(Math.max(0, area.height()), MeasureSpec.EXACTLY));
    }

    private void layoutArea(int index, Rect area) {
        View child = getChildAt(index);
        if (child == null) return;
        child.layout(area.left, area.top, area.left + Math.max(0, area.width()),
                area.top + Math.max(0, area.height()));
    }

    /** Fills mA/mB in view coordinates for a view of size w x h, honouring the padding. */
    private void computeAreas(int w, int h) {
        int[] loc = new int[2];
        getLocationOnScreen(loc);
        Point display = new Point();
        Display d = getDisplay();
        if (d != null) d.getRealSize(display);
        int left = getPaddingLeft(), top = getPaddingTop();
        int right = w - getPaddingRight(), bottom = h - getPaddingBottom();

        // The hinge band in screen coordinates: {start, end} along the axis that splits the panels.
        int bandStart = -1, bandEnd = -1;
        boolean stacked = display.y > display.x;
        int longSide = Math.max(display.x, display.y), shortSide = Math.min(display.x, display.y);
        for (int[] s : SPECS) {
            if (shortSide != s[2]) continue;
            if (longSide == 2 * s[0] + s[1]) {
                bandStart = s[0];
                bandEnd = s[0] + s[1];
            } else if (longSide == 2 * s[0]) {
                bandStart = s[0];
                bandEnd = s[0];
            }
        }
        mStacked = bandStart >= 0 ? stacked : h > w;
        if (bandStart >= 0) {
            // Screen to view coordinates.
            int offset = mStacked ? loc[1] : loc[0];
            bandStart -= offset;
            bandEnd -= offset;
        }
        int lo = mStacked ? top : left, hi = mStacked ? bottom : right;
        if (bandStart <= lo || bandEnd >= hi) {
            // Unknown geometry, or this window does not contain the hinge (it sits on one panel): split the view
            // itself, with a small gap.
            mStacked = h > w;
            lo = mStacked ? top : left;
            hi = mStacked ? bottom : right;
            int gap = (int) (16 * getResources().getDisplayMetrics().density);
            int mid = (lo + hi) / 2;
            bandStart = mid - gap / 2;
            bandEnd = mid + gap / 2;
        }
        if (mStacked) {
            mA.set(left, top, right, Math.max(top, Math.min(bottom, bandStart)));
            mB.set(left, Math.min(bottom, Math.max(top, bandEnd)), right, bottom);
        } else {
            mA.set(left, top, Math.max(left, Math.min(right, bandStart)), bottom);
            mB.set(Math.min(right, Math.max(left, bandEnd)), top, right, bottom);
        }
    }
}
