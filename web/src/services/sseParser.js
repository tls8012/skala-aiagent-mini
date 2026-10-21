/** UTF-8 디코딩 이후의 SSE 줄 파서. 청크/CRLF 경계와 여러 data 줄을 보존한다. */
export function createSseParser(onFrame) {
  let line = '', sawCR = false, data = [], event = 'message', id;
  function consume() {
    if (!line) {
      if (data.length) onFrame({ event, id, data: data.join('\n') });
      data = []; event = 'message'; id = undefined;
    } else if (!line.startsWith(':')) {
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'data') data.push(value);
      else if (field === 'event') event = value;
      else if (field === 'id' && !value.includes('\0')) id = value;
    }
    line = '';
  }
  return {
    feed(text) {
      for (const char of text) {
        if (sawCR && char === '\n') { sawCR = false; continue; }
        sawCR = false;
        if (char === '\r' || char === '\n') { consume(); sawCR = char === '\r'; }
        else line += char;
      }
    },
  };
}
