"use client";

import React, { useEffect, useRef } from "react";

interface WebGLVideoCanvasProps {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  active: boolean;
  contrast: number;
  brightness: number;
  saturation: number;
  vignette: number;
}

const VERTEX = `#version 300 es
in vec2 a_position;
out vec2 v_uv;
void main() {
  v_uv = vec2((a_position.x + 1.0) * 0.5, 1.0 - (a_position.y + 1.0) * 0.5);
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

const FRAGMENT = `#version 300 es
precision highp float;
uniform sampler2D u_video;
uniform float u_contrast;
uniform float u_brightness;
uniform float u_saturation;
uniform float u_vignette;
in vec2 v_uv;
out vec4 outColor;
void main() {
  vec4 color = texture(u_video, v_uv);
  color.rgb = (color.rgb - 0.5) * u_contrast + 0.5 + u_brightness;
  float luma = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
  color.rgb = mix(vec3(luma), color.rgb, u_saturation);
  float edge = smoothstep(0.82, 0.22, distance(v_uv, vec2(0.5)));
  color.rgb *= mix(1.0, edge, u_vignette);
  outColor = vec4(clamp(color.rgb, 0.0, 1.0), color.a);
}`;

export function WebGLVideoCanvas({
  videoRef,
  active,
  contrast,
  brightness,
  saturation,
  vignette,
}: WebGLVideoCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.style.opacity = "0";
    if (!active) return;
    const video = videoRef.current;
    if (!video) return;
    const gl = canvas.getContext("webgl2", { alpha: false, antialias: true });
    if (!gl) return;
    const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX);
    const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    if (!vertex || !fragment) return;
    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const position = gl.getAttribLocation(program, "a_position");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const uniforms = {
      contrast: gl.getUniformLocation(program, "u_contrast"),
      brightness: gl.getUniformLocation(program, "u_brightness"),
      saturation: gl.getUniformLocation(program, "u_saturation"),
      vignette: gl.getUniformLocation(program, "u_vignette"),
    };
    let frame = 0;
    let live = true;
    const render = () => {
      if (!live) return;
      const width = Math.max(
        2,
        canvas.clientWidth * Math.min(2, window.devicePixelRatio || 1),
      );
      const height = Math.max(
        2,
        canvas.clientHeight * Math.min(2, window.devicePixelRatio || 1),
      );
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.viewport(0, 0, canvas.width, canvas.height);
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        try {
          gl.bindTexture(gl.TEXTURE_2D, texture);
          gl.texImage2D(
            gl.TEXTURE_2D,
            0,
            gl.RGBA,
            gl.RGBA,
            gl.UNSIGNED_BYTE,
            video,
          );
          gl.uniform1f(uniforms.contrast, contrast);
          gl.uniform1f(uniforms.brightness, brightness);
          gl.uniform1f(uniforms.saturation, saturation);
          gl.uniform1f(uniforms.vignette, vignette);
          gl.drawArrays(gl.TRIANGLES, 0, 6);
          canvas.style.opacity = "1";
        } catch {
          canvas.style.opacity = "0";
        }
      }
      frame = window.requestAnimationFrame(render);
    };
    frame = window.requestAnimationFrame(render);
    return () => {
      live = false;
      window.cancelAnimationFrame(frame);
      gl.deleteTexture(texture);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
    };
  }, [active, brightness, contrast, saturation, videoRef, vignette]);

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-0 z-[1] h-full w-full opacity-0"
      aria-hidden="true"
    />
  );
}

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}
