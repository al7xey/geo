import { useEffect, useRef } from 'react';
import { fragmentShader } from './shader';

export function ShaderBackground() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current; if (!canvas) return;
    const host = canvas.parentElement;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { host?.classList.add('fallback'); return; }
    const gl = canvas.getContext('webgl', { alpha: true, antialias: false });
    if (!gl) { host?.classList.add('fallback'); return; }
    const shaders: WebGLShader[] = [];
    function shader(type: number, source: string) {
      const item = gl!.createShader(type); if (!item) throw new Error('WebGL unavailable');
      shaders.push(item); gl!.shaderSource(item, source); gl!.compileShader(item);
      if (!gl!.getShaderParameter(item, gl!.COMPILE_STATUS)) throw new Error('WebGL shader unavailable');
      return item;
    }
    let frame = 0; let program: WebGLProgram | null = null; let buffer: WebGLBuffer | null = null;
    let disposed = false; const pointer = { x: .5, y: .5 }; const mouse = { x: .5, y: .5 };
    let resize = () => {};
    const move = (event: MouseEvent) => { pointer.x = event.clientX / innerWidth; pointer.y = 1 - event.clientY / innerHeight; };
    try {
      program = gl.createProgram(); if (!program) throw new Error('WebGL program unavailable');
      gl.attachShader(program, shader(gl.VERTEX_SHADER, 'attribute vec2 position;varying vec2 vUv;void main(){vUv=(position+1.)*.5;gl_Position=vec4(position,0.,1.);}'));
      gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragmentShader)); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('WebGL link unavailable');
      gl.useProgram(program); buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
      const position = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      const uniform = (key: string) => gl.getUniformLocation(program!, key);
      const time = uniform('u_time'), resolution = uniform('u_resolution'), center = uniform('u_center'), mouseLocation = uniform('u_mouse');
      gl.uniform3f(uniform('u_colorCore'), 1, 1, 1); gl.uniform3f(uniform('u_colorFringe'), .2902, .5333, 1); gl.uniform1f(uniform('u_radius'), .75);
      resize = () => {
        const ratio = Math.min(devicePixelRatio, 1.5); canvas.width = Math.round(innerWidth * ratio); canvas.height = Math.round(innerHeight * ratio);
        gl.viewport(0,0,canvas.width,canvas.height); gl.uniform2f(resolution,canvas.width,canvas.height);
        const left = document.querySelector('.page.on .left')?.getBoundingClientRect();
        const card = document.querySelector('.page.on .card')?.getBoundingClientRect();
        const gap = left && card && card.left > left.right + 20 ? (left.right + card.left) / 2 : innerWidth * .5;
        gl.uniform2f(center, gap / innerHeight - .75, .5);
      };
      resize(); window.addEventListener('resize', resize); window.addEventListener('mousemove', move);
      const start = performance.now(); let last = 0;
      function draw(now: number) {
        if (disposed) return;
        frame = requestAnimationFrame(draw);
        if (document.hidden || now - last < 32) return;
        last = now; mouse.x += (pointer.x-mouse.x)*.05; mouse.y += (pointer.y-mouse.y)*.05;
        gl!.uniform1f(time, (now-start)/1000); gl!.uniform2f(mouseLocation, mouse.x, mouse.y);
        gl!.drawArrays(gl!.TRIANGLE_STRIP,0,4);
      }
      frame = requestAnimationFrame(draw);
    } catch { host?.classList.add('fallback'); }
    return () => { disposed = true; cancelAnimationFrame(frame); window.removeEventListener('resize', resize); window.removeEventListener('mousemove', move); if (buffer) gl.deleteBuffer(buffer); if (program) gl.deleteProgram(program); shaders.forEach(shader => gl.deleteShader(shader)); };
  }, []);
  return <div id="fx" aria-hidden="true"><canvas ref={ref} /></div>;
}
