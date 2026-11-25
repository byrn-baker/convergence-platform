import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../reviewer/public/reviewer.css', import.meta.url), 'utf8');

// The text of the block that starts at `opening`, and the sheet without that block.
function block(opening) {
  const start = css.indexOf(opening);
  assert.notEqual(start, -1, opening);
  let at = css.indexOf('{', start) + 1, depth = 1;
  for (; depth && at < css.length; at += 1) depth += css[at] === '{' ? 1 : css[at] === '}' ? -1 : 0;
  return {inside: css.slice(css.indexOf('{', start) + 1, at - 1), outside: css.slice(0, start) + css.slice(at)};
}

const root = block(':root');
const colours = Object.fromEntries(Array.from(root.inside.matchAll(/--([a-z]+):\s*(#[0-9a-f]{6});/g), found => [found[1], found[2]]));

function luminance(hex) {
  const [red, green, blue] = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16) / 255)
    .map(value => value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}
function contrast(first, second) {
  const [light, dark] = [luminance(colours[first]), luminance(colours[second])].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

test('every colour in the reviewer sheet is a named custom property', () => {
  assert.deepEqual(Object.keys(colours).sort(),
    ['control', 'cyan', 'foreground', 'grid', 'ground', 'line', 'magenta', 'muted', 'panel', 'selected', 'threat']);
  assert.doesNotMatch(root.outside, /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i);
});

test('text meets WCAG AA contrast on every surface it sits on', () => {
  for (const text of ['foreground', 'muted', 'cyan', 'magenta', 'threat']) {
    for (const surface of ['ground', 'panel', 'selected']) {
      assert.ok(contrast(text, surface) >= 4.5, `${text} on ${surface}: ${contrast(text, surface).toFixed(2)}`);
    }
  }
  // Pressed and submit buttons put ground-coloured text on cyan.
  assert.ok(contrast('ground', 'cyan') >= 4.5);
});

test('control borders and the focus ring meet the 3:1 non-text minimum', () => {
  for (const surface of ['ground', 'panel']) {
    assert.ok(contrast('control', surface) >= 3, `control on ${surface}: ${contrast('control', surface).toFixed(2)}`);
    assert.ok(contrast('cyan', surface) >= 3);
  }
  assert.match(css, /(?:input, select, textarea|button) \{[^}]*border: 1px solid var\(--control\)/);
});

test('glow, transitions and the sweep exist only when motion is not reduced', () => {
  const motion = block('@media (prefers-reduced-motion: no-preference)');
  assert.match(motion.inside, /animation:/);
  assert.match(motion.inside, /text-shadow:/);
  assert.doesNotMatch(motion.outside, /animation|@keyframes|transition|text-shadow|blink/);
  // The sweep is slow and sits behind the content; nothing flashes.
  assert.match(motion.inside, /animation: sweep 14s linear infinite/);
  assert.match(motion.inside, /z-index: -1/);
  assert.match(motion.inside, /pointer-events: none/);
});

test('the sheet loads nothing the content security policy forbids', () => {
  assert.doesNotMatch(css, /url\(|@import|@font-face/);
});

test('the phone layout stacks list rows and keeps 44 px targets', () => {
  const phone = block('@media (max-width: 40rem)').inside;
  assert.match(phone, /\.table-wrap tr \{\s*display: grid/);
  assert.match(phone, /content: attr\(data-label\)/);
  assert.match(phone, /\.table-wrap td\.primary a \{[^}]*min-height: 2\.75rem/);
  assert.match(css, /\nbutton \{[^}]*min-height: 2\.75rem/);
  assert.match(css, /input, select, textarea \{[^}]*min-height: 2\.75rem/);
});
