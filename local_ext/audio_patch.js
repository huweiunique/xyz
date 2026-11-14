/* 每次打开静音起播；首页与详情仅共享当前页面会话的声音选择。 */
(function () {
  'use strict';
  if (window.HGAudio) return;
  var muted = true, installed = false;
  function apply() {
    document.querySelectorAll('#video, video.bgvid').forEach(function (video) {
      video.defaultMuted = muted;
      video.muted = muted;
    });
    if (typeof window.updateMuteBtns === 'function') window.updateMuteBtns();
  }
  function set(value) { muted = !!value; apply(); }
  window.HGAudio = {
    install: function (store) {
      if (installed) return;
      installed = true;
      var get = store.get, save = store.set;
      // 复用上游读写接缝；旧 hg_muted 记录不再决定起播声音，其他设置仍正常持久化。
      store.get = function (key, fallback) {
        return key === 'hg_muted' ? muted : get.apply(this, arguments);
      };
      store.set = function (key, value) {
        if (key === 'hg_muted') set(value);
        else return save.apply(this, arguments);
      };
      apply();
      window.addEventListener('pageshow', function (event) {
        if (event.persisted) set(true);
      });
    },
    toggle: function () { set(!muted); }
  };
})();
