// Development-only capture of the real UI while the test process stays hidden.
using System;
using System.IO;
using System.Linq;
using UnityEngine;

internal static class UiCapture
{
    internal static void Capture(string path)
    {
        const int width = 2560, height = 1440;
        var canvases = UnityEngine.Object.FindObjectsByType<Canvas>(FindObjectsInactive.Exclude, FindObjectsSortMode.None)
            .Where(c => c.isRootCanvas && c.isActiveAndEnabled && c.renderMode == RenderMode.ScreenSpaceOverlay)
            .Select(c => new CanvasState(c)).ToArray();
        if (canvases.Length == 0) throw new InvalidOperationException("Offscreen UI capture found no active overlay canvas.");
        var previous = RenderTexture.active;
        GameObject cameraObject = null;
        RenderTexture target = null;
        Texture2D pixels = null;
        try
        {
            target = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
            target.Create();
            cameraObject = new GameObject("HotkeyManager_QA_Camera", typeof(Camera));
            cameraObject.hideFlags = HideFlags.HideAndDontSave;
            var camera = cameraObject.GetComponent<Camera>();
            camera.enabled = false;
            camera.targetTexture = target;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(.025f, .027f, .039f, 1);
            camera.orthographic = true;
            camera.orthographicSize = 360;
            camera.nearClipPlane = .1f; camera.farClipPlane = 10;
            camera.allowHDR = false; camera.allowMSAA = false;
            // Keep world geometry outside the capture camera's short clip range.
            camera.transform.position = new Vector3(0, 0, -10000);
            foreach (var state in canvases)
            {
                state.Canvas.renderMode = RenderMode.ScreenSpaceCamera;
                state.Canvas.worldCamera = camera;
                state.Canvas.planeDistance = 1;
            }
            Canvas.ForceUpdateCanvases();
            camera.Render();
            RenderTexture.active = target;
            pixels = new Texture2D(width, height, TextureFormat.RGBA32, false);
            pixels.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
            pixels.Apply(false, false);
            File.WriteAllBytes(path, pixels.EncodeToPNG());
        }
        finally
        {
            foreach (var state in canvases) state.Restore();
            Canvas.ForceUpdateCanvases();
            RenderTexture.active = previous;
            if (cameraObject != null)
            {
                cameraObject.GetComponent<Camera>().targetTexture = null;
                UnityEngine.Object.Destroy(cameraObject);
            }
            if (target != null) { target.Release(); UnityEngine.Object.Destroy(target); }
            if (pixels != null) UnityEngine.Object.Destroy(pixels);
        }
    }

    sealed class CanvasState
    {
        internal readonly Canvas Canvas;
        readonly RenderMode mode;
        readonly Camera camera;
        readonly float distance;
        readonly Vector3 position, scale;
        readonly Quaternion rotation;
        internal CanvasState(Canvas canvas)
        {
            Canvas = canvas; mode = canvas.renderMode; camera = canvas.worldCamera; distance = canvas.planeDistance;
            position = canvas.transform.localPosition; rotation = canvas.transform.localRotation; scale = canvas.transform.localScale;
        }
        internal void Restore()
        {
            if (Canvas == null) return;
            Canvas.renderMode = mode; Canvas.worldCamera = camera; Canvas.planeDistance = distance;
            Canvas.transform.localPosition = position; Canvas.transform.localRotation = rotation; Canvas.transform.localScale = scale;
        }
    }
}
