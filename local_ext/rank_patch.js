/* 跟随源站三类榜单，使用 video_id 接入原有详情与播放链路。 */
(function () {
  'use strict';
  if (window.HGRanks) return;
  var types = [
    {type:'hot', title:'热播榜'},
    {type:'recommend', title:'推荐榜'},
    {type:'potential', title:'飙升榜'}
  ];
  function select(type) {
    document.querySelectorAll('#rankTabs .chip').forEach(function (tab) {
      var active = tab.dataset.type === type;
      tab.classList.toggle('on', active);
      tab.setAttribute('aria-pressed', String(active));
    });
  }
  window.HGRanks = {
    init: function () {
      var tabs = document.getElementById('rankTabs');
      if (!tabs) return;
      tabs.innerHTML = types.map(function (item) {
        return '<button type="button" class="chip" data-type="' + item.type + '">' + item.title + '</button>';
      }).join('');
      select('hot');
    },
    load: async function (type) {
      if (!types.some(function (item) { return item.type === type; })) type = 'hot';
      select(type);
      var myRun = ++runToken.rank, box = document.getElementById('rankList');
      box.setAttribute('aria-busy', 'true');
      box.innerHTML = '<div class="empty">加载中...</div>';
      try {
        var result = await api('/api/ranks/' + type);
        if (myRun !== runToken.rank) return;
        var items = itemsOf(result).filter(function (item) {
          var id = Number(item.video_id == null ? item.id : item.video_id);
          return Number.isSafeInteger(id) && id > 0;
        });
        if (!items.length) {
          box.innerHTML = '<div class="empty">' + esc(result.msg || '该榜单暂无数据') + '</div>';
          return;
        }
        box.innerHTML = items.map(function (item, index) {
          var id = Number(item.video_id == null ? item.id : item.video_id);
          var rank = Number(item.rank);
          var metric = item.metric_label && item.metric_value != null ?
            esc(item.metric_label) + ' ' + esc(fmtN(item.metric_value)) : '';
          var episodes = item.episode_count || item.total_episodes;
          var detail = [metric, episodes ? esc(episodes) + '集' : ''].filter(Boolean).join(' · ');
          return '<div class="rankrow" role="button" tabindex="0" data-id="' + id + '"><div class="no">' +
            (Number.isSafeInteger(rank) && rank > 0 ? rank : index + 1) + '</div>' + coverImg(item.cover, '', item.title) +
            '<div><h4>' + esc(item.title || '') + '</h4><p>' + detail + '</p></div></div>';
        }).join('');
        bindClicks(box);
        box.querySelectorAll('.rankrow').forEach(function (row) {
          row.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.click(); }
          });
        });
      } catch (error) {
        if (myRun === runToken.rank) box.innerHTML = '<div class="empty">加载失败：' + esc(error.message || error) + '</div>';
      } finally {
        if (myRun === runToken.rank) box.setAttribute('aria-busy', 'false');
      }
    }
  };
})();
