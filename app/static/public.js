"use strict";

let isLive = false;
LiveCap.emptyText = () => (isLive ? "Listening. Captions will appear here when someone speaks." : "The live captions have not started yet. This page updates by itself.");
LiveCap.onState = (state) => {
  isLive = state.live;
  $("live-dot").className = `dot ${isLive ? "live" : ""}`;
  $("status-text").textContent = isLive ? "Live" : state.waiting ? "Waiting for the stream to start" : "Not live yet";
};
LiveCap.onDisconnect = () => {
  $("live-dot").className = "dot bad";
  $("status-text").textContent = "Reconnecting…";
};
startCaptions();
