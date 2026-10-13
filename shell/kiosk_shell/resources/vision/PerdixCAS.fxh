// PERDIX Vision sharpening for gamescope's ReShade support: contrast adaptive sharpening, the
// algorithm of AMD FidelityFX CAS (MIT licence, (c) Advanced Micro Devices), at native resolution.
// PERDIX writes a small perdix_cas_<strength>.fx that sets PERDIX_SHARPNESS (0-1) and includes this
// file; gamescope applies it to the game image every frame on the GPU, inside its own composition.
// Self-contained: no ReShade.fxh needed.

#ifndef PERDIX_SHARPNESS
#define PERDIX_SHARPNESS 0.5
#endif

texture PerdixBackBufferTex : COLOR;
sampler PerdixBackBuffer
{
    Texture = PerdixBackBufferTex;
    MagFilter = POINT;
    MinFilter = POINT;
    MipFilter = POINT;
};

void PerdixFullscreenVS(in uint id : SV_VertexID, out float4 position : SV_Position, out float2 texcoord : TEXCOORD)
{
    texcoord.x = (id == 2) ? 2.0 : 0.0;
    texcoord.y = (id == 1) ? 2.0 : 0.0;
    position = float4(texcoord * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
}

float3 PerdixCASPS(float4 vpos : SV_Position, float2 texcoord : TEXCOORD) : SV_Target
{
    // 3x3 neighbourhood:  a b c / d e f / g h i
    float3 a = tex2D(PerdixBackBuffer, texcoord, int2(-1, -1)).rgb;
    float3 b = tex2D(PerdixBackBuffer, texcoord, int2( 0, -1)).rgb;
    float3 c = tex2D(PerdixBackBuffer, texcoord, int2( 1, -1)).rgb;
    float3 d = tex2D(PerdixBackBuffer, texcoord, int2(-1,  0)).rgb;
    float3 e = tex2D(PerdixBackBuffer, texcoord).rgb;
    float3 f = tex2D(PerdixBackBuffer, texcoord, int2( 1,  0)).rgb;
    float3 g = tex2D(PerdixBackBuffer, texcoord, int2(-1,  1)).rgb;
    float3 h = tex2D(PerdixBackBuffer, texcoord, int2( 0,  1)).rgb;
    float3 i = tex2D(PerdixBackBuffer, texcoord, int2( 1,  1)).rgb;

    // Soft minimum and maximum of the cross and the full 3x3 (CAS).
    float3 mn = min(min(min(d, e), min(f, b)), h);
    mn += min(mn, min(min(a, c), min(g, i)));
    float3 mx = max(max(max(d, e), max(f, b)), h);
    mx += max(mx, max(max(a, c), max(g, i)));

    // How far the pixel is from clipping decides how much it may be sharpened: flat areas and
    // edges near black or white get less, so there is no halo or noise boost.
    float3 amp = saturate(min(mn, 2.0 - mx) / mx);
    amp = sqrt(amp);
    float peak = -1.0 / lerp(8.0, 5.0, PERDIX_SHARPNESS);
    float3 w = amp * peak;
    float3 sharpened = saturate(((b + d + f + h) * w + e) / (1.0 + 4.0 * w));
    return lerp(e, sharpened, PERDIX_SHARPNESS);
}

technique PerdixCAS
{
    pass
    {
        VertexShader = PerdixFullscreenVS;
        PixelShader = PerdixCASPS;
    }
}
