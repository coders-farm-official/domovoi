/* Entry for the vendored Univer bundle (web/static/vendor/univer/univer.js).
 * Open-source Univer only (Apache-2.0): the sheets core, sort and filter
 * presets. Nothing from @univerjs-pro. The bundle carries its own
 * production React 18.3.1; the dashboard's React is never touched.
 * web/static/sheet_editor.jsx reads everything from window.DomovoiUniverLib. */
import { createUniver, LocaleType, mergeLocales } from '@univerjs/presets';
import { orangeTheme, defaultTheme } from '@univerjs/themes';
import { ALL_IMPLEMENTED_FUNCTIONS, ErrorValueObject } from '@univerjs/engine-formula';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import coreEnUS from '@univerjs/preset-sheets-core/locales/en-US';
import { UniverSheetsSortPreset } from '@univerjs/preset-sheets-sort';
import sortEnUS from '@univerjs/preset-sheets-sort/locales/en-US';
import { UniverSheetsFilterPreset } from '@univerjs/preset-sheets-filter';
import filterEnUS from '@univerjs/preset-sheets-filter/locales/en-US';
import { imageUrlAllowed, makeSafeImage } from './safe_image.js';

const builtinImage = ALL_IMPLEMENTED_FUNCTIONS.find((f) => f[1] === 'IMAGE');
// Registered after the built-ins (engine-formula FormulaController), so this
// replaces IMAGE rather than adding a second one.
const formulaFunctions = builtinImage
  ? [[makeSafeImage(builtinImage[0], ErrorValueObject, () => window.location.origin,
      () => window.location.href), 'IMAGE']]
  : [];

window.DomovoiUniverLib = {
  version: '1.2.0',
  createUniver,
  LocaleType,
  mergeLocales,
  themes: { orange: orangeTheme, default: defaultTheme },
  UniverSheetsCorePreset,
  UniverSheetsSortPreset,
  UniverSheetsFilterPreset,
  formulaFunctions,
  imageUrlAllowed,
  locales: { 'en-US': mergeLocales(coreEnUS, sortEnUS, filterEnUS) },
};
window.dispatchEvent(new Event('domovoi-univer-ready'));
