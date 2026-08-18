import numpy as np
import subprocess
import os

# SMPTE ST 2084 (PQ) OETF
# Maps absolute luminance in nits (L) to normalized non-linear PQ code value (0.0 to 1.0)
def linear_to_pq(l_nits):
    l = np.clip(l_nits / 10000.0, 0, 1)
    m1 = 2610 / 16384
    m2 = 2523 / 32
    c1 = 3424 / 4096
    c2 = 2413 / 128
    c3 = 2392 / 128
    return ((c1 + c2 * (l ** m1)) / (1 + c3 * (l ** m1))) ** m2

def create_patch(width, height, r_nits, g_nits, b_nits):
    r_pq = linear_to_pq(r_nits)
    g_pq = linear_to_pq(g_nits)
    b_pq = linear_to_pq(b_nits)
    
    # 10-bit RGB
    r_10 = np.clip(np.round(r_pq * 1023), 0, 1023).astype(np.uint16)
    g_10 = np.clip(np.round(g_pq * 1023), 0, 1023).astype(np.uint16)
    b_10 = np.clip(np.round(b_pq * 1023), 0, 1023).astype(np.uint16)
    
    patch = np.zeros((height, width, 3), dtype=np.uint16)
    patch[:,:,0] = r_10
    patch[:,:,1] = g_10
    patch[:,:,2] = b_10
    return patch

def generate_video(filename, patches, max_cll=1000):
    width = 1280
    height = 720
    frames = 24
    
    cols = min(len(patches), 5)
    rows = (len(patches) + cols - 1) // cols
    
    patch_w = width // cols
    patch_h = height // rows
    
    img = np.zeros((height, width, 3), dtype=np.uint16)
    
    for i, (name, r, g, b) in enumerate(patches):
        r_idx = i // cols
        c_idx = i % cols
        p = create_patch(patch_w, patch_h, r, g, b)
        
        y = r_idx * patch_h
        x = c_idx * patch_w
        
        h = min(patch_h, height - y)
        w = min(patch_w, width - x)
        
        img[y:y+h, x:x+w] = p[:h, :w]
        
    raw_path = filename + ".raw"
    with open(raw_path, 'wb') as f:
        img_bytes = img.tobytes()
        for _ in range(frames):
            f.write(img_bytes)
            
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo",
        "-video_size", f"{width}x{height}",
        "-pixel_format", "rgb48le",
        "-framerate", "24",
        "-i", raw_path,
        "-vf", "scale=in_color_matrix=bt2020:out_color_matrix=bt2020,format=yuv420p10le",
        "-c:v", "libx265",
        "-x265-params", f"colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:max-cll={max_cll},400:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
        filename
    ]
    
    subprocess.run(cmd, check=True)
    os.remove(raw_path)
    print(f"Generated {filename}")

if __name__ == "__main__":
    os.makedirs("tests/clips", exist_ok=True)
    
    # 1. Luminance scale (Neutral Axis & Monotonicity & Identity)
    lum_patches = [
        ("0.001", 0.001, 0.001, 0.001),
        ("0.01", 0.01, 0.01, 0.01),
        ("0.1", 0.1, 0.1, 0.1),
        ("1", 1, 1, 1),
        ("10", 10, 10, 10),
        ("100", 100, 100, 100),
        ("203", 203, 203, 203),
        ("400", 400, 400, 400),
        ("1000", 1000, 1000, 1000),
        ("4000", 4000, 4000, 4000),
    ]
    generate_video("tests/clips/luma_steps.mkv", lum_patches, max_cll=4000)
    
    # 2. Gamut patches (Primaries, Secondaries, Grays)
    gamut_patches = [
        ("White_100", 1000, 1000, 1000),
        ("Gray_50", 500, 500, 500),
        ("Gray_25", 250, 250, 250),
        ("R_100", 1000, 0, 0),
        ("G_100", 0, 1000, 0),
        ("B_100", 0, 0, 1000),
        ("C_100", 0, 1000, 1000),
        ("M_100", 1000, 0, 1000),
        ("Y_100", 1000, 1000, 0),
        ("R_50", 500, 0, 0),
    ]
    generate_video("tests/clips/gamut_test.mkv", gamut_patches, max_cll=1000)
