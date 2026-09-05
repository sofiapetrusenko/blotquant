import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LaneEditor } from '@/app/analyze/LaneEditor';
import { laneListIssues, laneRoiStrings, type LaneDraft } from '@/app/analyze/lanes';
import type { Roi } from '@/lib/result';

/**
 * The lane table and the drawing surface are two views of one list, asserted in both directions.
 *
 * This is the binding the whole step depends on: a number typed into the table must move the
 * rectangle, and a rectangle drawn on the picture must write the numbers. A screen where those
 * two drifted would show a person one region and measure another, and the drift would be
 * invisible until the result came back over the wrong pixels.
 *
 * The assertions are on actual numbers -- `width="40"` on the SVG rectangle, `10` in the `x`
 * input -- not on "something changed".
 *
 * jsdom lays nothing out, so `getBoundingClientRect` is stubbed to the image's own size. That
 * makes the surface 1:1 with image pixels, which is what lets a drag from (10,10) to (60,50)
 * assert an exact rectangle instead of an approximate one.
 */

/** A real gallery card's dimensions, so the coordinate arithmetic is exercised at a real scale. */
const BOUNDS = { width: 489, height: 122 };

const FIRST_LANE: LaneDraft = { key: 'lane-a', x: 123, y: 0, width: 33, height: 122 };
const SECOND_LANE: LaneDraft = { key: 'lane-b', x: 200, y: 0, width: 30, height: 122 };

function Harness({
  initial,
  onDetect = () => undefined,
  detectedBands = [],
}: {
  initial: LaneDraft[];
  onDetect?: () => void;
  detectedBands?: Roi[];
}): React.ReactElement {
  const [lanes, setLanes] = useState<LaneDraft[]>(initial);
  return (
    <LaneEditor
      imageSrc="blob:test-image"
      bounds={BOUNDS}
      lanes={lanes}
      onChange={setLanes}
      onDetect={onDetect}
      detecting={false}
      busy={false}
      detectedBands={detectedBands}
      issues={laneListIssues(lanes, BOUNDS)}
    />
  );
}

function submittedOrder(): string[] {
  return Array.from(screen.getByTestId('submitted-order').querySelectorAll('li')).map(
    (item) => item.textContent ?? '',
  );
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: BOUNDS.width,
    bottom: BOUNDS.height,
    width: BOUNDS.width,
    height: BOUNDS.height,
    toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('table → rectangle', () => {
  it('moves the drawn rectangle when the w input is typed into', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE]} />);

    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('width', '33');

    const widthInput = screen.getByLabelText('Lane 1 w');
    await user.clear(widthInput);
    await user.type(widthInput, '40');

    expect(widthInput).toHaveValue(40);
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('width', '40');
    // x, y and h are untouched: editing one field edits one field.
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('x', '123');
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('height', '122');
    expect(submittedOrder()).toEqual(['lane_roi=123,0,40,122']);
  });

  it('reports a rectangle typed past the image edge instead of correcting it', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE]} />);

    const xInput = screen.getByLabelText('Lane 1 x');
    await user.clear(xInput);
    await user.type(xInput, '470');

    // Not silently clamped: the typed value stands, on screen and in what would be submitted.
    expect(xInput).toHaveValue(470);
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('x', '470');
    expect(submittedOrder()).toEqual(['lane_roi=470,0,33,122']);

    const issues = screen.getByTestId('lane-issues');
    expect(issues.textContent).toContain('past the image');
    expect(issues.textContent).toContain('489');
    expect(issues.textContent).toContain('Reduce width to 19');
  });
});

describe('rectangle → table', () => {
  it('writes the drawn rectangle into the numeric inputs', () => {
    render(<Harness initial={[FIRST_LANE]} />);

    const surface = screen.getByTestId('lane-surface');
    fireEvent.mouseDown(surface, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(window, { clientX: 60, clientY: 50 });

    // While the drag is live it is a draft, not a lane: the table still holds one row.
    expect(screen.getByTestId('lane-rect-draft')).toHaveAttribute('width', '50');
    expect(screen.queryByLabelText('Lane 2 x')).toBeNull();

    fireEvent.mouseUp(window);

    expect(screen.getByLabelText('Lane 2 x')).toHaveValue(10);
    expect(screen.getByLabelText('Lane 2 y')).toHaveValue(10);
    expect(screen.getByLabelText('Lane 2 w')).toHaveValue(50);
    expect(screen.getByLabelText('Lane 2 h')).toHaveValue(40);
    expect(screen.getByTestId('lane-rect-1')).toHaveAttribute('x', '10');
    expect(screen.getByTestId('lane-rect-1')).toHaveAttribute('height', '40');
    expect(submittedOrder()).toEqual(['lane_roi=123,0,33,122', 'lane_roi=10,10,50,40']);
  });

  it('normalises a drag made upwards and to the left', () => {
    render(<Harness initial={[]} />);

    const surface = screen.getByTestId('lane-surface');
    fireEvent.mouseDown(surface, { clientX: 60, clientY: 50 });
    fireEvent.mouseMove(window, { clientX: 10, clientY: 10 });
    fireEvent.mouseUp(window);

    expect(screen.getByLabelText('Lane 1 x')).toHaveValue(10);
    expect(screen.getByLabelText('Lane 1 y')).toHaveValue(10);
    expect(screen.getByLabelText('Lane 1 w')).toHaveValue(50);
    expect(screen.getByLabelText('Lane 1 h')).toHaveValue(40);
  });
});

describe('the submitted order is the table order', () => {
  it('changes when a lane is moved down', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE, SECOND_LANE]} />);

    expect(submittedOrder()).toEqual([
      'lane_roi=123,0,33,122',
      'lane_roi=200,0,30,122',
    ]);

    await user.click(screen.getByLabelText('Move lane 1 down'));

    expect(submittedOrder()).toEqual([
      'lane_roi=200,0,30,122',
      'lane_roi=123,0,33,122',
    ]);
    // The rectangles follow the rows, so row 1 and rect 0 still name the same region.
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('x', '200');
    expect(screen.getByLabelText('Lane 1 x')).toHaveValue(200);
  });

  it('refuses to move the first lane up or the last lane down', () => {
    render(<Harness initial={[FIRST_LANE, SECOND_LANE]} />);
    expect(screen.getByLabelText('Move lane 1 up')).toBeDisabled();
    expect(screen.getByLabelText('Move lane 2 down')).toBeDisabled();
  });

  it('drops the deleted lane and renumbers the rest', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE, SECOND_LANE]} />);

    await user.click(screen.getByLabelText('Delete lane 1'));

    expect(submittedOrder()).toEqual(['lane_roi=200,0,30,122']);
    expect(screen.getByLabelText('Lane 1 x')).toHaveValue(200);
    expect(screen.queryByLabelText('Lane 2 x')).toBeNull();
  });

  it('adds a lane covering the whole image', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[]} />);

    await user.click(screen.getByRole('button', { name: 'Add lane' }));

    expect(submittedOrder()).toEqual([`lane_roi=0,0,${BOUNDS.width},${BOUNDS.height}`]);
    expect(screen.getByLabelText('Lane 1 w')).toHaveValue(BOUNDS.width);
  });
});

describe('an incomplete row', () => {
  it('is not shown as a submittable lane_roi string', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE]} />);
    await user.clear(screen.getByLabelText('Lane 1 w'));

    // `lane_roi=123,0,NaN,122` is a value that would never be sent; showing it invites the
    // reader to believe it would be.
    expect(submittedOrder()).toEqual(['(incomplete — fill in every field)']);
    expect(screen.getByTestId('lane-issues').textContent).toContain('width is empty');
    expect(screen.queryByTestId('lane-rect-0')).toBeNull();
  });
});

describe('the zero-lane state says what is wrong', () => {
  it('shows the actionable message, not just a count', () => {
    render(<Harness initial={[]} />);
    const issues = screen.getByTestId('lane-issues');
    expect(issues.textContent).toContain('No lane has been added');
    expect(issues.textContent).toContain('Add lane');
    // The message written in `laneListIssues` used to be counted and never displayed: the
    // editor rendered per-lane issues only, and an empty list has no lanes to have issues.
    expect(laneListIssues([], BOUNDS)).toHaveLength(1);
  });
});

describe('a drag that leaves the picture', () => {
  it('is clamped to the image, because a pointer outside it named no coordinate', () => {
    render(<Harness initial={[]} />);
    const surface = screen.getByTestId('lane-surface');
    // Start inside, drag well past the bottom-right corner.
    fireEvent.mouseDown(surface, { clientX: 400, clientY: 100 });
    fireEvent.mouseMove(window, { clientX: 900, clientY: 400 });
    fireEvent.mouseUp(window);

    expect(screen.getByLabelText('Lane 1 x')).toHaveValue(400);
    expect(screen.getByLabelText('Lane 1 w')).toHaveValue(BOUNDS.width - 400);
    expect(screen.getByLabelText('Lane 1 h')).toHaveValue(BOUNDS.height - 100);
    // Clamped, so the rectangle is inside the image and nothing is reported.
    expect(screen.getByTestId('lane-issues').textContent).toBe('');
  });

  it('still produces a lane with at least one pixel in each direction from a bare click', () => {
    render(<Harness initial={[]} />);
    const surface = screen.getByTestId('lane-surface');
    fireEvent.mouseDown(surface, { clientX: 50, clientY: 50 });
    fireEvent.mouseUp(window);

    expect(screen.getByLabelText('Lane 1 w')).toHaveValue(1);
    expect(screen.getByLabelText('Lane 1 h')).toHaveValue(1);
    expect(screen.getByTestId('lane-issues').textContent).toBe('');
  });

  it('is not started by a secondary mouse button', () => {
    render(<Harness initial={[]} />);
    const surface = screen.getByTestId('lane-surface');
    fireEvent.mouseDown(surface, { clientX: 10, clientY: 10, button: 2 });
    fireEvent.mouseMove(window, { clientX: 60, clientY: 50 });
    fireEvent.mouseUp(window);

    expect(screen.queryByLabelText('Lane 1 x')).toBeNull();
  });
});

describe('every lane operation is reachable without a pointer', () => {
  /**
   * Driven by Tab and Enter rather than by `user.click`, which dispatches a click on an element
   * whether or not anything could have focused it. What is asserted is that the controls are in
   * the tab order and respond to Enter — the part that is this component's to get right.
   *
   * Arrow-key stepping of a `<input type="number">` is deliberately *not* asserted: that is the
   * browser's behaviour on a native control, and jsdom does not implement it, so a test of it
   * here would be a test of jsdom.
   */
  it('reaches and activates reorder and delete with Tab and Enter', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE, SECOND_LANE]} />);

    const down = screen.getByLabelText('Move lane 1 down');
    down.focus();
    expect(down).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(submittedOrder()).toEqual(['lane_roi=200,0,30,122', 'lane_roi=123,0,33,122']);

    const remove = screen.getByLabelText('Delete lane 1');
    remove.focus();
    expect(remove).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(submittedOrder()).toEqual(['lane_roi=123,0,33,122']);
  });

  it('puts every numeric field in the tab order with its own accessible name', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE]} />);

    screen.getByLabelText('Lane 1 x').focus();
    for (const field of ['y', 'w', 'h']) {
      await user.tab();
      expect(screen.getByLabelText(`Lane 1 ${field}`)).toHaveFocus();
    }
  });

  it('types a coordinate through the keyboard and moves the rectangle', async () => {
    const user = userEvent.setup();
    render(<Harness initial={[FIRST_LANE]} />);
    const width = screen.getByLabelText('Lane 1 w');
    width.focus();
    await user.keyboard('{Control>}a{/Control}45');
    expect(width).toHaveValue(45);
    expect(screen.getByTestId('lane-rect-0')).toHaveAttribute('width', '45');
  });
});

describe('laneRoiStrings', () => {
  it('is the only place the form values are built, and follows the list order', () => {
    expect(laneRoiStrings([FIRST_LANE, SECOND_LANE])).toEqual([
      '123,0,33,122',
      '200,0,30,122',
    ]);
    expect(laneRoiStrings([SECOND_LANE, FIRST_LANE])).toEqual([
      '200,0,30,122',
      '123,0,33,122',
    ]);
  });
});
