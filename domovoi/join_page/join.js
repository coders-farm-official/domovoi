/* The join page's logic (domovoi/join_page/join.html). Plain ES5 and
 * XMLHttpRequest on purpose: it has to run on an old phone's browser.
 *
 * - The room code (domovoi-room:1?core=…&fp=…&room=…, satellite/enrolment.py)
 *   is the URL's fragment. It is read here only to show the room's name,
 *   to check it names THIS server (else it is refused, plainly), and to
 *   hand it, unchanged, to the app; the app checks it in full
 *   (RoomEnrolment.kt) and makes the server prove its key. Shown with
 *   textContent only.
 * - What to download comes from GET /v1/android-app on this same server
 *   (domovoi/android_app.py public_status): the release and, per ABI,
 *   whether this server holds a checked copy.
 * - The app's "update available" notice (android/…/update/AppUpdate.kt)
 *   opens this page as /join?abi=<abi>&update=1: the app knows the phone's
 *   own processor, so that APK goes first when this server holds it, and
 *   the page is worded for a phone that already has the app.
 * - Step 2 is an Android intent: link to the app's domovoi-room: handler
 *   (package com.domovoi.app only), falling back to this page with
 *   ?app=missing when the app isn't installed. Firefox gets the plain
 *   domovoi-room: link.
 */
(function () {
  'use strict';

  var PACKAGE = 'com.domovoi.app';
  var MAX_CODE = 1100;

  function $(id) { return document.getElementById(id); }
  function show(id, on) { var el = $(id); if (el) el.hidden = !on; }
  function text(id, value) { var el = $(id); if (el) el.textContent = value; }
  function mb(bytes) { return Math.max(1, Math.round(bytes / 1e6)) + ' MB'; }

  // The fragment as written in the URL (location.hash is decoded by some
  // old browsers; href never is).
  function fragment() {
    var href = String(window.location.href);
    var i = href.indexOf('#');
    return i < 0 ? '' : href.slice(i + 1);
  }

  // Just enough of the room code to name the room. Null when there is
  // none; {bad: true} when it can't be read.
  function readCode(raw) {
    if (!raw) return null;
    if (raw.length > MAX_CODE) return { bad: true };
    var m = /^domovoi-room:1\?(.+)$/i.exec(raw);
    if (!m) return { bad: true };
    var fields = {};
    var pairs = m[1].split('&');
    for (var i = 0; i < pairs.length; i++) {
      var eq = pairs[i].indexOf('=');
      if (eq <= 0) return { bad: true };
      try {
        fields[pairs[i].slice(0, eq)] = decodeURIComponent(pairs[i].slice(eq + 1));
      } catch (e) {
        return { bad: true };
      }
    }
    if (!fields.core || !fields.fp || !fields.room) return { bad: true };
    return { payload: raw, room: fields.room, core: fields.core };
  }

  // The page's own address with the fallback marker, for Chrome to come
  // back to when the app isn't installed.
  function fallbackUrl(payload) {
    return window.location.protocol + '//' + window.location.host + '/join?app=missing#' + payload;
  }

  function roomLink(payload) {
    var rest = payload.slice(payload.indexOf(':') + 1);
    if (/Firefox\//.test(navigator.userAgent)) return 'domovoi-room:' + rest;
    return 'intent:' + rest + '#Intent;scheme=domovoi-room;package=' + PACKAGE +
      ';S.browser_fallback_url=' + encodeURIComponent(fallbackUrl(payload)) + ';end';
  }

  // A 32-bit phone, when the browser says so honestly. "armv7l" is a
  // 32-bit system: only 32-bit apps run there. Anything else (aarch64,
  // armv8l = a 32-bit browser on a 64-bit phone, or today's frozen
  // "Linux armv81") tells nothing, so "most phones" stays first.
  function looks32bit() {
    var p = String(navigator.platform || '') + ' ' + String(navigator.userAgent || '');
    return /armv7l|armv7 /i.test(p);
  }

  // The room code's server, as scheme://host[:port], or null. The URL
  // parser where the browser has one (it writes IPv6 and default ports the
  // way location does), else the text lower-cased.
  function origin(url) {
    try {
      var u = new URL(url);
      return u.protocol + '//' + u.host;
    } catch (e) {
      var m = /^(https?:\/\/[^\/?#]+)\/?$/i.exec(url);
      return m ? m[1].toLowerCase() : null;
    }
  }

  // A room code made by THIS server names this server: the page refuses
  // one naming another (a link someone built to send the phone elsewhere),
  // rather than hand it to the app. The app checks it in full anyway.
  function sameServer(core) {
    return origin(core) === (window.location.protocol + '//' + window.location.host).toLowerCase();
  }

  // The ABI the app asked for (?abi=), when it is one a release carries.
  function askedAbi() {
    var m = /[?&]abi=(arm64-v8a|armeabi-v7a)(&|$)/.exec(window.location.search);
    return m ? m[1] : null;
  }

  function isAndroid() { return /Android/i.test(String(navigator.userAgent || '')); }

  function get(path, done) {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', path, true);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status !== 200) { done(new Error('HTTP ' + (xhr.status || 'error'))); return; }
      var body;
      try { body = JSON.parse(xhr.responseText); } catch (e) { done(e); return; }
      done(null, body);
    };
    xhr.send(null);
  }

  function stateLine(message, warn) {
    var el = $('install-state');
    el.textContent = message;
    el.className = warn ? 'state warn' : 'state';
    el.hidden = false;
  }

  function renderApp(err, app, code) {
    if (err) {
      stateLine('Couldn’t ask this server about the app (' + err.message + '). Reload the page in a moment.', true);
      return;
    }
    var already = code && !code.bad ? ' If the Domovoi app is already on this phone, go on to step 2.' : '';
    if (!app || !app.release) {
      text('install-title', 'No app release yet');
      stateLine('There is no Domovoi app release yet. When there is one, this server offers it here.' + already, true);
      return;
    }
    var apks = {};
    for (var i = 0; i < (app.apks || []).length; i++) apks[app.apks[i].abi] = app.apks[i];
    var main64 = apks['arm64-v8a'];
    var main32 = apks['armeabi-v7a'];
    var ready64 = main64 && main64.ready;
    var ready32 = main32 && main32.ready;
    if (!ready64 && !ready32) {
      stateLine('The app isn’t on this server yet. Ask whoever runs Domovoi to press “Download the app ' +
        'to this server” on the dashboard (Settings → Phones & browsers), then reload this page.' + already, true);
      return;
    }
    show('install-state', false);
    var first = ready64 ? main64 : main32;
    var second = ready64 ? (ready32 ? main32 : null) : null;
    var asked = askedAbi();
    var picked = (asked === 'armeabi-v7a' && ready32) || (asked === 'arm64-v8a' && ready64);
    if (picked) {
      first = apks[asked];
      second = asked === 'armeabi-v7a' ? (ready64 ? main64 : null) : (ready32 ? main32 : null);
    } else if (looks32bit() && ready32) { first = main32; second = ready64 ? main64 : null; }
    var label = { 'arm64-v8a': 'most phones', 'armeabi-v7a': 'older or cheaper phones (32-bit)' };
    var main = $('dl-main');
    main.href = first.path;
    main.setAttribute('download', first.file);
    main.textContent = first.abi === 'armeabi-v7a' ? 'download the 32-bit app' : 'download the app';
    text('dl-main-sub', 'For ' + label[first.abi] + (picked ? ': the one the app chose for this phone'
      : first.abi === 'armeabi-v7a' && looks32bit() ? ': this phone looks like a 32-bit one' : '') +
      ' · ' + mb(first.size));
    if (second) {
      var other = $('dl-other');
      other.href = second.path;
      other.setAttribute('download', second.file);
      other.textContent = (second.abi === 'armeabi-v7a'
        ? 'Older or cheaper phone? Download the 32-bit app'
        : 'A newer phone? Download the app for most phones') + ' (' + mb(second.size) + ')';
    } else {
      show('dl-other-line', false);
    }
    text('version', app.release.version_name);
    show('skip-install', !!(code && !code.bad));
    var caption = $('picture-4');
    var num = document.createElement('b');
    num.textContent = '4.';
    caption.textContent = '';
    caption.appendChild(num);
    caption.appendChild(document.createTextNode(code && !code.bad
      ? ' Come back to this page in the browser for step 2.'
      : ' Open Domovoi from the home screen.'));
    show('downloads', true);
    // A household phone's next step, once there is an app to install (an
    // update has none: the app is set up already).
    show('step-connect', !code && !updating());
  }

  // Opened by the app's update notice (?update=1), with no room code.
  function updating() {
    return /[?&]update=1(&|$)/.test(window.location.search) && !fragment();
  }

  function main() {
    var code = readCode(fragment());
    var missingApp = /[?&]app=missing(&|$)/.test(window.location.search);
    show('not-android', !isAndroid());
    text('server-line', 'From the Domovoi server at ' + window.location.host + '.');
    if (updating()) {
      text('title', 'Update the Domovoi app');
      text('lede', 'A newer Domovoi app for this phone, straight from this home’s own Domovoi server. ' +
        'It installs over the one you have: your servers and settings stay.');
      text('install-title', 'Install the update');
    }
    if (code && !code.bad && !sameServer(code.core)) {
      text('other-server', 'This room code is for the Domovoi server at ' + code.core.replace(/^https?:\/\//i, '') +
        ', not this one (' + window.location.host + '). Scan the code on this home\u2019s dashboard again, ' +
        'or open Domovoi Room on the phone and scan it there.');
      show('other-server', true);
      code = { bad: true };
    } else if (code && code.bad) {
      show('bad-code', true);
    }
    if (code && !code.bad) {
      document.title = 'Domovoi room: ' + code.room;
      text('title', 'Make this phone a Domovoi room');
      text('lede', 'It becomes the room “' + code.room + '” of this home’s Domovoi. Two steps, about ten minutes, on the phone’s charger and your home Wi-Fi. Phones as rooms are a preview: they work, but have been tried on fewer phones than the rest of Domovoi.');
      text('room-name', '“' + code.room + '”');
      $('open-room').href = roomLink(code.payload);
      show('step-room', true);
      show('missing-app', missingApp);
    }
    get('/v1/android-app', function (err, app) { renderApp(err, app, code); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main);
  else main();
})();
