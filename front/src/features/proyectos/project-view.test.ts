import { describe, expect, it } from 'vitest';
import { linkProvider } from './project-view';

describe('linkProvider', () => {
  it('nombra solo los hosts exactos que el backend baja', () => {
    expect(linkProvider('https://www.youtube.com/watch?v=abc')).toBe('YouTube');
    expect(linkProvider('https://youtube.com/watch?v=abc')).toBe('YouTube');
    expect(linkProvider('https://youtu.be/abc')).toBe('YouTube');
    expect(linkProvider('https://player.vimeo.com/video/1')).toBe('Vimeo');
    expect(linkProvider('https://clips.twitch.tv/Slug')).toBe('Twitch');
    expect(linkProvider('https://drive.google.com/file/d/x/view')).toBe('Google Drive');
  });

  it('otro subdominio o un host que solo termina igual no es del sitio', () => {
    for (const url of [
      'https://youtube.com.evil.com/watch?v=abc',
      'https://evilyoutube.com/watch?v=abc',
      'https://consent.youtube.com/m?continue=http://10.0.0.1/',
      'https://x.youtu.be/abc',
      'https://evil.drive.google.com/file/d/x',
      'https://sites.google.com/x',
      'https://example.com/youtube.com',
      'no es un link',
    ]) {
      expect(linkProvider(url), url).toBeNull();
    }
  });
});
