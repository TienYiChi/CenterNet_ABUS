import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import argparse
import numpy as np
import torch
from data.abus_data import AbusNpyFormat
from models.networks.hourglass import get_large_hourglass_net

if not torch.cuda.is_available():
    print('CUDA is unavailable, abort mission!')
    quit()
else:
    device = torch.device('cuda:0')

parser = argparse.ArgumentParser()

# parser.add_argument(
#     '--save_dir', '-s', type=str, required=True,
#     help='Specify where to save visualized volume as series of images.'
# )
params = parser.parse_args()

def main():
    heads = {
        'hm': 2,
        'wh': 3
    }
    model = get_large_hourglass_net(heads, n_stacks=1)
    model = model.to(device)
    
    all_data = AbusNpyFormat(root, train=False, validation=False)
    data, label = all_data.__getitem__(0)
    data = torch.unsqueeze(data, 0).to(device)
    
    output = model(data)
    return

if __name__=='__main__':
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), 'data/sys_ucc/')
    main()