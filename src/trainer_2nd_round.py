import os, sys, time
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim.lr_scheduler import ExponentialLR
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import DataLoader
from apex import amp
from models.networks.hourglass import get_large_hourglass_net
from models.loss import FocalLoss, RegL1Loss, RegL2Loss
from data.abus_data import AbusNpyFormat

use_cuda = torch.cuda.is_available()
device = torch.device("cuda" if use_cuda else "cpu")
torch.cuda.empty_cache()

def train(args):
    print('Preparing...')
    validset = AbusNpyFormat(root, crx_valid=True, crx_fold_num=args.crx_valid, crx_partition='valid', augmentation=False, include_fp=True)
    trainset = AbusNpyFormat(root, crx_valid=True, crx_fold_num=args.crx_valid, crx_partition='train', augmentation=True, include_fp=True)
    trainset_loader = DataLoader(trainset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    validset_loader = DataLoader(validset, batch_size=1, shuffle=False, num_workers=0)

    crit_hm = FocalLoss()
    crit_wh = RegL1Loss()

    train_hist = {
        'train_loss':[],
        'valid_hm_loss':[],
        'valid_wh_loss':[],
        'valid_total_loss':[],
        'per_epoch_time':[]
    }

    heads = {
        'hm': 1,
        'wh': 3,
        'fp_hm': 1
    }
    model = get_large_hourglass_net(heads, n_stacks=1)

    init_ep = 0
    end_ep = args.max_epoch
    print('Resume training from the designated checkpoint.')
    path = pre_dir + 'hourglass_' + 'f{}_frz'.format(args.crx_valid)
    pretrained_dict = torch.load(path)
    model_dict = model.state_dict()

    # 1. filter out unnecessary keys
    pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
    # 2. overwrite entries in the existing state dict
    model_dict.update(pretrained_dict) 
    # 3. load the new state dict
    model.load_state_dict(model_dict)

    if args.freeze:
        for param in model.pre.parameters():
            param.requires_grad = False
        for param in model.kps.parameters():
            param.requires_grad = False
        for param in model.cnvs.parameters():
            param.requires_grad = False
        for param in model.inters.parameters():
            param.requires_grad = False
        for param in model.inters_.parameters():
            param.requires_grad = False
        for param in model.cnvs_.parameters():
            param.requires_grad = False
        for param in model.wh.parameters():
            param.requires_grad = False
        for param in model.hm.parameters():
            param.requires_grad = False
        
    optimizer = optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=args.lr)
    optim_sched = ExponentialLR(optimizer, 0.92, last_epoch=-1)
    model.to(device)
    model, optimizer = amp.initialize(model, optimizer, opt_level="O1")

    print('Preparation done.')
    print('******************')
    print('Training starts...')

    start_time = time.time()
    min_loss = 0

    first_ep = True
    for epoch in range(init_ep, end_ep):
        train_loss = 0
        valid_hm_loss = 0
        epoch_start_time = time.time()
        lambda_s = args.lambda_s # * (1.03**epoch)

        # Training
        model.train()
        optimizer.zero_grad()
        for batch_idx, (data_img, data_hm, data_wh, _) in enumerate(trainset_loader):
            if use_cuda:
                data_img = data_img.cuda()
                data_hm = data_hm.cuda()
                data_wh = data_wh.cuda()
            output = model(data_img)
            hm_loss = crit_hm(output[-1]['fp_hm'], data_hm)

            total_loss = hm_loss
            train_loss += hm_loss.item()
            with amp.scale_loss(total_loss, optimizer) as scaled_loss:
                scaled_loss.backward()

            if  (first_ep and batch_idx < 10) or ((batch_idx % 8) is 0) or (batch_idx == len(trainset_loader) - 1):
                print('Gradient applied at batch #', batch_idx)
                optimizer.step()
                optimizer.zero_grad()
            
            print("Epoch: [{:2d}] [{:3d}], hm_loss: {:.3f}"\
                .format((epoch + 1), (batch_idx + 1), hm_loss.item()))
        
        optim_sched.step()

        # Validation
        model.eval()
        with torch.no_grad():
            for batch_idx, (data_img, data_hm, data_wh, _) in enumerate(validset_loader):
                if use_cuda:
                    data_img = data_img.cuda()
                    data_hm = data_hm.cuda()
                    data_wh = data_wh.cuda()
                output = model(data_img)
                hm_loss = crit_hm(output[-1]['fp_hm'], data_hm)

                valid_hm_loss += hm_loss.item()

        valid_hm_loss = valid_hm_loss/validset.__len__()
        train_loss = train_loss/trainset.__len__()

        if epoch == 0 or valid_hm_loss < min_loss:
            min_loss = valid_hm_loss
            model.save(str(epoch))
        elif (epoch % 5) == 4:
            model.save(str(epoch))
        model.save('latest')

        train_hist['per_epoch_time'].append(time.time() - epoch_start_time)
        train_hist['valid_hm_loss'].append(valid_hm_loss)
        train_hist['train_loss'].append(train_loss)
        plt.figure()
        plt.plot(train_hist['train_loss'], color='k')
        plt.plot(train_hist['valid_total_loss'], color='r')
        plt.plot(train_hist['valid_hm_loss'], color='b')
        plt.ylabel('Loss')
        plt.xlabel('Epoch')
        plt.savefig('loss_fold{}.png'.format(args.crx_valid))
        plt.close()

        print("Epoch: [{:d}], valid_hm_loss: {:.3f}".format((epoch + 1), valid_hm_loss))
        print('Epoch exec time: {} min'.format((time.time() - epoch_start_time)/60))
        first_ep = False

    print("Training finished.")
    print("Total time cost: {} min.".format((time.time() - start_time)/60))


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--crx_valid', type=int, default=0)
    parser.add_argument('--batch_size', type=int, default=2)
    parser.add_argument('--max_epoch', type=int, default=60)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--lambda_s', type=float, default=0.1)
    parser.add_argument('--resume', type=bool, default=False)
    parser.add_argument('--resume_ep', type=int, default=0)
    parser.add_argument('--freeze', dest='freeze', action='store_true')
    parser.add_argument('--no-freeze', dest='freeze', action='store_false')
    parser.set_defaults(freeze=False)
    return parser.parse_args()

if __name__ == '__main__':
    if use_cuda:
        print('GPU is available.')

    args = _parse_args()
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'data/sys_ucc/')
    chkpts_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'checkpoints/')
    pre_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'pretrained/')
    if not os.path.exists(chkpts_dir):
        os.makedirs(chkpts_dir)

    train(args)