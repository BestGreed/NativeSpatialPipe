#pragma once
/*
 *      Copyright (C) 2014-2015 Team KODI
 *      Copyright (C) 2007-2010 Christian Kothe
 *      http://kodi.tv
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>
 *
 */

/*
 * passed upon fsurround available under
 * http://www.hydrogenaud.io/forums/index.php?showtopic=52235
 * written by pro_optimizer
 */

#include <vector>
#include <map>

enum AE_DSP_CHANNEL_PRESENT : unsigned { AE_DSP_PRSNT_CH_UNDEFINED=0, AE_DSP_PRSNT_CH_BC=1u<<0, AE_DSP_PRSNT_CH_BL=1u<<1, AE_DSP_PRSNT_CH_BLOC=1u<<2, AE_DSP_PRSNT_CH_BR=1u<<3, AE_DSP_PRSNT_CH_BROC=1u<<4, AE_DSP_PRSNT_CH_FC=1u<<5, AE_DSP_PRSNT_CH_FL=1u<<6, AE_DSP_PRSNT_CH_FLOC=1u<<7, AE_DSP_PRSNT_CH_FR=1u<<8, AE_DSP_PRSNT_CH_FROC=1u<<9, AE_DSP_PRSNT_CH_LFE=1u<<10, AE_DSP_PRSNT_CH_SL=1u<<11, AE_DSP_PRSNT_CH_SR=1u<<12, AE_DSP_PRSNT_CH_TBL=1u<<13, AE_DSP_PRSNT_CH_TBR=1u<<14, AE_DSP_PRSNT_CH_TFL=1u<<15, AE_DSP_PRSNT_CH_TFR=1u<<16 };

/*
 * Identifier translation from FreeSurround to KODI ADSP
 *
 * ci_front_left          = AE_DSP_PRSNT_CH_FL
 * ci_front_center_left   = AE_DSP_PRSNT_CH_FLOC
 * ci_front_center        = AE_DSP_PRSNT_CH_FC
 * ci_front_center_right  = AE_DSP_PRSNT_CH_FROC
 * ci_front_right         = AE_DSP_PRSNT_CH_FR
 * ci_side_front_left     = AE_DSP_PRSNT_CH_TFL
 * ci_side_front_right    = AE_DSP_PRSNT_CH_TFR
 * ci_side_center_left    = AE_DSP_PRSNT_CH_SL
 * ci_side_center_right   = AE_DSP_PRSNT_CH_SR
 * ci_side_back_left      = AE_DSP_PRSNT_CH_TBL
 * ci_side_back_right     = AE_DSP_PRSNT_CH_TBR
 * ci_back_left           = AE_DSP_PRSNT_CH_BL
 * ci_back_center_left    = AE_DSP_PRSNT_CH_BLOC
 * ci_back_center         = AE_DSP_PRSNT_CH_BC
 * ci_back_center_right   = AE_DSP_PRSNT_CH_BROC
 * ci_back_right          = AE_DSP_PRSNT_CH_BR
 * ci_lfe                 = AE_DSP_PRSNT_CH_LFE
 */

const int grid_res = 21; // resolution of the lookup grid

// channel allocation maps (per setup)
typedef std::vector<std::vector<float*> > alloc_lut;
extern std::map<unsigned,alloc_lut> chn_alloc;
// channel metadata maps (per setup)
extern std::map<unsigned,std::vector<float> > chn_angle;
extern std::map<unsigned,std::vector<float> > chn_xsf;
extern std::map<unsigned,std::vector<float> > chn_ysf;
extern std::map<unsigned,std::vector<AE_DSP_CHANNEL_PRESENT> > chn_id;
extern bool init_maps();
